"""Dispatcharr entry point for Logo Finder (Schedules Direct and tv-logos)."""

from __future__ import annotations

import os

try:  # Loaded by Dispatcharr as ``<plugin package>.plugin``.
    from . import tvlogos
    from .core import (
        SOURCE_TVLOGOS,
        ChannelView,
        decide_channels,
        is_numeric_station_id,
        parse_overrides,
        parse_sources,
        should_apply,
        summary,
        write_report,
    )
except ImportError:  # pragma: no cover - direct module import for local checks
    import tvlogos  # type: ignore
    from core import (  # type: ignore
        SOURCE_TVLOGOS,
        ChannelView,
        decide_channels,
        is_numeric_station_id,
        parse_overrides,
        parse_sources,
        should_apply,
        summary,
        write_report,
    )


FIELDS = [
    {
        "id": "profile",
        "label": "Channel profile",
        "type": "string",
        "default": "fixed",
        "help_text": "Only channels in this channel profile are considered.",
    },
    {
        "id": "style",
        "label": "Logo style",
        "type": "select",
        "default": "dark",
        "options": [
            {"value": "dark", "label": "Dark"},
            {"value": "light", "label": "Light"},
            {"value": "white", "label": "White"},
            {"value": "gray", "label": "Gray"},
        ],
    },
    {
        "id": "sources",
        "label": "Sources",
        "type": "string",
        "default": "sd,tvlogos",
        "help_text": (
            "Comma-separated sources in order: sd (Schedules Direct), "
            "tvlogos (tv-logo/tv-logos). Overrides always run first."
        ),
    },
    {
        "id": "default_country",
        "label": "Default country",
        "type": "string",
        "default": "us",
        "help_text": (
            "Two-letter country code used when the channel's tvg_id has no "
            "country suffix (for example us, uk, ca)."
        ),
    },
    {
        "id": "overrides",
        "label": "Overrides",
        "type": "text",
        "default": "{}",
        "help_text": (
            "JSON object keyed by channel number: a URL to use, or \"keep\" "
            "to leave the channel alone."
        ),
    },
]

ACTIONS = [
    {
        "id": "preview",
        "label": "Preview",
        "description": "Report the proposed logo changes without writing anything.",
        "button_label": "Preview",
        "button_variant": "outline",
        "button_color": "blue",
        "async": True,
    },
    {
        "id": "apply",
        "label": "Apply",
        "description": (
            "Assign the proposed logos and write the report. Existing logos are "
            "never deleted."
        ),
        "button_label": "Apply",
        "button_variant": "filled",
        "button_color": "blue",
        "async": True,
    },
]


def _log(context, level: str, message: str) -> None:
    logger = context.get("logger")
    if logger is not None:
        getattr(logger, level, logger.info)(message)


def _channels_in_profile(profile_name: str) -> list:
    """Return the profile's channel rows paired with their decision view."""
    from apps.channels.models import ChannelProfile, ChannelProfileMembership

    profile = ChannelProfile.objects.filter(name=profile_name).first()
    if profile is None:
        raise ValueError(f"channel profile {profile_name!r} does not exist")

    memberships = (
        ChannelProfileMembership.objects.filter(channel_profile=profile)
        .select_related("channel__logo", "channel__epg_data")
        .order_by("channel__channel_number")
    )

    pairs = []
    for membership in memberships:
        channel = membership.channel
        view = ChannelView(
            number=channel.channel_number,
            name=channel.name,
            current_logo_url=channel.logo.url if channel.logo else None,
            station_id=channel.epg_data.tvg_id if channel.epg_data else None,
            tvg_id=channel.tvg_id,
        )
        pairs.append((channel, view))
    return pairs


def _sd_logos(station_ids) -> dict:
    """Map numeric station id -> SD ``icon_url`` list from every SD source."""
    from apps.epg.models import EPGData, EPGSource

    wanted = {station_id.strip() for station_id in station_ids if is_numeric_station_id(station_id)}
    if not wanted:
        return {}

    source_ids = list(
        EPGSource.objects.filter(source_type="schedules_direct").values_list("id", flat=True)
    )
    if not source_ids:
        return {}

    logos = {}
    rows = (
        EPGData.objects.filter(epg_source_id__in=source_ids, tvg_id__in=wanted)
        .exclude(icon_url__isnull=True)
        .exclude(icon_url="")
        .values_list("tvg_id", "icon_url")
    )
    for tvg_id, icon_url in rows:
        logos.setdefault((tvg_id or "").strip(), []).append(icon_url)
    return logos


def _apply_decisions(pairs, decisions) -> int:
    """Assign logos for replace/override rows in one transaction."""
    from django.db import transaction
    from apps.channels.models import Logo

    applied = 0
    with transaction.atomic():
        for (channel, _view), decision in zip(pairs, decisions):
            if not should_apply(decision):
                continue
            logo, _created = Logo.objects.get_or_create(
                url=decision.candidate_url,
                defaults={"name": channel.name},
            )
            if channel.logo_id != logo.pk:
                channel.logo = logo
                channel.save(update_fields=["logo"])
                applied += 1
    return applied


class Plugin:
    name = "Logo Finder"
    version = "0.2.0"
    description = (
        "Find channel logos in a preferred style from Schedules Direct and "
        "tv-logos and apply them after a dry-run report."
    )
    author = "tylertufano"
    help_url = "https://github.com/tylertufano/dispatcharr-logo-finder"

    fields = FIELDS
    actions = ACTIONS

    def run(self, action, params, context):
        if action not in ("preview", "apply"):
            return {"status": "error", "message": f"Unknown action {action!r}"}

        settings = context.get("settings") or {}
        profile_name = (settings.get("profile") or "fixed").strip() or "fixed"
        style = (settings.get("style") or "dark").strip() or "dark"
        default_country = (settings.get("default_country") or "us").strip().lower() or "us"

        try:
            overrides = parse_overrides(settings.get("overrides", "{}"))
            sources = parse_sources(settings.get("sources"))
        except ValueError as exc:
            _log(context, "error", f"Logo Finder: {exc}")
            return {"status": "error", "message": str(exc)}

        data_dir = context.get("data_dir")
        tvlogos_index = None
        tvlogos_info = None
        if SOURCE_TVLOGOS in sources:
            try:
                tree = tvlogos.fetch_tree(data_dir)
                tvlogos_index = tvlogos.build_index(tree["paths"])
                tvlogos_info = {
                    "files": len(tvlogos_index),
                    "cache_age_hours": round(tree["cache_age_seconds"] / 3600.0, 1),
                    "from_cache": tree["from_cache"],
                    "stale": tree["stale"],
                }
                if tree.get("error"):
                    tvlogos_info["error"] = tree["error"]
                _log(
                    context,
                    "info",
                    f"Logo Finder: tv-logos tree has {len(tvlogos_index)} PNGs, "
                    f"cache age {tvlogos_info['cache_age_hours']} h"
                    + (" (stale)" if tree["stale"] else ""),
                )
            except Exception as exc:  # noqa: BLE001 - fall back to Schedules Direct
                _log(
                    context,
                    "warning",
                    f"Logo Finder: tv-logos unavailable, using Schedules Direct only: {exc}",
                )
                tvlogos_info = {"error": str(exc)}

        try:
            pairs = _channels_in_profile(profile_name)
            sd_logos = _sd_logos([view.station_id for _channel, view in pairs])
            decisions = decide_channels(
                [view for _channel, view in pairs],
                style,
                overrides,
                sd_logos,
                tvlogos_index,
                sources,
                default_country,
            )
        except Exception as exc:  # noqa: BLE001 - surface a useful action result
            _log(context, "exception", f"Logo Finder: {exc}")
            return {"status": "error", "message": str(exc)}

        counts = summary(decisions)

        try:
            data_dir = context["data_dir"]
            os.makedirs(data_dir, exist_ok=True)
            report_paths = write_report(data_dir, decisions, action)
        except Exception as exc:  # noqa: BLE001
            _log(context, "exception", f"Logo Finder: could not write report: {exc}")
            return {"status": "error", "message": f"could not write report: {exc}"}

        applied = 0
        if action == "apply" and counts["replace"] + counts["override"]:
            try:
                applied = _apply_decisions(pairs, decisions)
            except Exception as exc:  # noqa: BLE001
                _log(context, "exception", f"Logo Finder: apply failed: {exc}")
                return {
                    "status": "error",
                    "message": f"apply failed, no changes committed: {exc}",
                    "summary": counts,
                    "report": report_paths,
                }

        detail = (
            f"{counts['replace']} replace, {counts['override']} override, "
            f"{counts['keep_already_preferred']} already preferred, "
            f"{counts['keep_generic']} generic only, "
            f"{counts['keep_no_match']} no match."
        )
        message = f"Preview: {detail}"
        if action == "apply":
            message = f"Applied {applied} logo change(s): {detail}"
        if tvlogos_info and "error" in tvlogos_info:
            message += f" tv-logos unavailable: {tvlogos_info['error']}"
        elif tvlogos_info:
            message += (
                f" tv-logos tree: {tvlogos_info['files']} files, "
                f"cache age {tvlogos_info['cache_age_hours']} h."
            )

        return {
            "status": "ok",
            "message": message,
            "summary": counts,
            "applied": applied,
            "report": report_paths,
            "tvlogos": tvlogos_info,
        }
