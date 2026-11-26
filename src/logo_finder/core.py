"""Pure decision and report logic for the Logo Finder plugin.

This module deliberately has no Django imports so the decision rules can be
tested with plain dataclasses. ``plugin.py`` builds the Dispatcharr models and
feeds them through these functions.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping, Optional, Sequence

try:  # Loaded by Dispatcharr as ``<plugin package>.core``.
    from . import tvlogos
except ImportError:  # pragma: no cover - direct module import for local checks
    import tvlogos  # type: ignore

SD_LOGO_HOST = "logos.json.schedulesdirect.org"
REPLACE = "replace"
KEEP_ALREADY = "keep: already preferred"
KEEP_NO_MATCH = "keep: no match"
KEEP_GENERIC = "keep: generic only"
OVERRIDE = "override"

SOURCE_SD = "sd"
SOURCE_OVERRIDE = "override"
SOURCE_TVLOGOS = "tvlogos"

VALID_SOURCES = (SOURCE_SD, SOURCE_TVLOGOS)
DEFAULT_SOURCES = (SOURCE_SD, SOURCE_TVLOGOS)

CSV_FIELDS = (
    "channel_number",
    "channel_name",
    "current_url",
    "candidate_url",
    "source",
    "decision",
    "reason",
)


@dataclass(frozen=True)
class ChannelView:
    """The channel facts the decision rules need, free of Django."""

    number: float
    name: str
    current_logo_url: Optional[str] = None
    station_id: Optional[str] = None
    tvg_id: Optional[str] = None


@dataclass(frozen=True)
class LogoDecision:
    """One row of the preview report."""

    channel_number: str
    channel_name: str
    current_url: str
    candidate_url: str
    source: str
    decision: str
    reason: str

    def as_row(self) -> dict:
        return asdict(self)


def format_channel_number(number) -> str:
    """Render a channel number the way the overrides keys expect (582, not 582.0)."""
    if number is None:
        return ""
    value = float(number)
    if value.is_integer():
        return str(int(value))
    return f"{value:g}".rstrip(".")


def normalize_override_key(key) -> str:
    """Normalise an override key so ``582`` and ``582.0`` address the same channel."""
    text = str(key).strip()
    try:
        value = float(text)
    except (TypeError, ValueError):
        return text
    if value.is_integer():
        return str(int(value))
    return f"{value:g}".rstrip(".")


def parse_overrides(raw) -> dict:
    """Parse the ``overrides`` setting into ``{channel number: url | "keep"}``.

    ``raw`` is the JSON string from the plugin settings. An empty value means
    no overrides. Raises ``ValueError`` with a user-facing message on bad input.
    """
    if raw is None:
        return {}
    if isinstance(raw, str):
        if not raw.strip():
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"overrides is not valid JSON: {exc}") from exc
    else:
        data = raw

    if not isinstance(data, dict):
        raise ValueError('overrides must be a JSON object like {"582": "https://..."}')

    overrides = {}
    for key, value in data.items():
        if not isinstance(value, str):
            raise ValueError(f"override for channel {key!r} must be a string")
        overrides[normalize_override_key(key)] = value.strip()
    return overrides


def parse_sources(raw) -> tuple:
    """Parse the ``sources`` setting into an ordered tuple of enabled sources.

    ``raw`` is the comma-separated setting value; ``None`` or an empty value
    means the default (Schedules Direct, then tv-logos). Overrides always run
    first and are not part of this list.
    """
    if raw is None:
        return DEFAULT_SOURCES
    if isinstance(raw, str):
        items = [part.strip().lower() for part in raw.replace(";", ",").split(",")]
    else:
        items = [str(part).strip().lower() for part in raw]
    items = [item for item in items if item]
    if not items:
        return DEFAULT_SOURCES
    unknown = sorted({item for item in items if item not in VALID_SOURCES})
    if unknown:
        raise ValueError(
            f"sources has unknown value(s) {', '.join(unknown)}; "
            f"use {', '.join(VALID_SOURCES)}"
        )
    return tuple(source for source in VALID_SOURCES if source in items)


def is_numeric_station_id(station_id: Optional[str]) -> bool:
    """Gracenote station ids shared by Schedules Direct and Zap2XML are numeric."""
    if not isinstance(station_id, str):
        return False
    return station_id.strip().isdigit()


def has_style_marker(url: Optional[str], style: str) -> bool:
    return bool(url) and f"_{style}_" in url


def is_preferred_logo(url: Optional[str], style: str) -> bool:
    """True when the current logo already is an Schedules Direct logo of this style."""
    return bool(url) and SD_LOGO_HOST in url and has_style_marker(url, style)


def _candidate_urls(value) -> Iterable[str]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(url for url in value if isinstance(url, str))


def sd_candidate(
    station_id: Optional[str],
    style: str,
    sd_logos: Mapping[str, object],
) -> Optional[str]:
    """Return the SD logo URL for a numeric station id in the requested style."""
    if not is_numeric_station_id(station_id):
        return None
    for url in _candidate_urls(sd_logos.get(station_id.strip())):
        if has_style_marker(url, style):
            return url
    return None


def decide_channel(
    channel: ChannelView,
    style: str,
    overrides: Mapping[str, str],
    sd_logos: Mapping[str, object],
    tvlogos_index=None,
    sources=None,
    default_country: str = "us",
) -> LogoDecision:
    """Apply override, source order, style and idempotency rules to one channel."""
    number = format_channel_number(channel.number)
    current = (channel.current_logo_url or "").strip()
    name = channel.name or ""

    override = overrides.get(number)
    if override is not None:
        if override.lower() == "keep":
            return LogoDecision(
                number,
                name,
                current,
                "",
                SOURCE_OVERRIDE,
                OVERRIDE,
                "override: keep current logo",
            )
        return LogoDecision(
            number,
            name,
            current,
            override,
            SOURCE_OVERRIDE,
            OVERRIDE,
            "override: use configured URL",
        )

    enabled = parse_sources(sources)
    sd_available = SOURCE_SD in enabled
    tvlogos_available = SOURCE_TVLOGOS in enabled and tvlogos_index is not None

    sd_logo = sd_candidate(channel.station_id, style, sd_logos) if sd_available else None
    tv_match = None
    if tvlogos_available:
        tv_match = tvlogos.match(
            tvlogos_index,
            name,
            channel.tvg_id or channel.station_id,
            style,
            default_country,
        )
    tv_exact = tv_match.url if tv_match is not None and tv_match.is_exact else ""
    tv_generic = tv_match if tv_match is not None and not tv_match.is_exact else None

    if is_preferred_logo(current, style):
        return LogoDecision(
            number,
            name,
            current,
            sd_logo or tv_exact,
            SOURCE_SD,
            KEEP_ALREADY,
            f"current logo is already an SD {style} logo",
        )

    if tvlogos_available and tvlogos.is_preferred_url(current, name, tvlogos_index):
        return LogoDecision(
            number,
            name,
            current,
            sd_logo or tv_exact,
            SOURCE_TVLOGOS,
            KEEP_ALREADY,
            "current logo is already a tv-logos file for this channel",
        )

    if sd_logo is not None:
        return LogoDecision(
            number,
            name,
            current,
            sd_logo,
            SOURCE_SD,
            REPLACE,
            f"SD {style} logo available",
        )

    if tv_exact:
        return LogoDecision(
            number,
            name,
            current,
            tv_exact,
            SOURCE_TVLOGOS,
            REPLACE,
            f"tv-logos exact match: {tv_match.path}",
        )

    if tv_generic is not None:
        return LogoDecision(
            number,
            name,
            current,
            "",
            SOURCE_TVLOGOS,
            KEEP_GENERIC,
            f"generic only: {tv_generic.reason}",
        )

    if not sd_available:
        reason = "sd source is disabled and no tv-logos exact match"
    elif is_numeric_station_id(channel.station_id):
        reason = f"no SD {style} logo for station {channel.station_id.strip()}"
        if tvlogos_available:
            reason += " and no tv-logos exact match"
    else:
        reason = "channel has no numeric Gracenote station id"
        if tvlogos_available:
            reason += " and no tv-logos exact match"
    return LogoDecision(
        number,
        name,
        current,
        "",
        SOURCE_TVLOGOS if not sd_available else SOURCE_SD,
        KEEP_NO_MATCH,
        reason,
    )


def decide_channels(
    channels: Sequence[ChannelView],
    style: str,
    overrides: Mapping[str, str],
    sd_logos: Mapping[str, object],
    tvlogos_index=None,
    sources=None,
    default_country: str = "us",
) -> list:
    enabled = parse_sources(sources)
    return [
        decide_channel(
            channel,
            style,
            overrides,
            sd_logos,
            tvlogos_index,
            enabled,
            default_country,
        )
        for channel in channels
    ]


def should_apply(decision: LogoDecision) -> bool:
    return decision.decision in (REPLACE, OVERRIDE) and bool(decision.candidate_url)


def summary(decisions: Iterable[LogoDecision]) -> dict:
    counts = {
        "total": 0,
        "replace": 0,
        "override": 0,
        "keep_already_preferred": 0,
        "keep_generic": 0,
        "keep_no_match": 0,
    }
    for decision in decisions:
        counts["total"] += 1
        if decision.decision == REPLACE:
            counts["replace"] += 1
        elif decision.decision == OVERRIDE:
            counts["override"] += 1
        elif decision.decision == KEEP_ALREADY:
            counts["keep_already_preferred"] += 1
        elif decision.decision == KEEP_GENERIC:
            counts["keep_generic"] += 1
        else:
            counts["keep_no_match"] += 1
    return counts


def report_rows(decisions: Iterable[LogoDecision]) -> list:
    return [decision.as_row() for decision in decisions]


def write_report(data_dir: str, decisions: Iterable[LogoDecision], mode: str) -> dict:
    """Write the JSON and CSV report into the plugin data dir and return their paths."""
    rows = report_rows(decisions)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"logo-finder-{mode}-{stamp}"
    json_path = f"{data_dir.rstrip('/')}/{base}.json"
    csv_path = f"{data_dir.rstrip('/')}/{base}.csv"

    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(rows, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    with open(csv_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    return {"json": json_path, "csv": csv_path}
