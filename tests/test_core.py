import os
import sys
import unittest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "logo_finder")
)

import core  # noqa: E402
import tvlogos  # noqa: E402

DARK = "https://logos.json.schedulesdirect.org/stationLogos/s56783_dark_360w_270h.png?v=9a56b7b950fb8f7293d2"
LIGHT = "https://logos.json.schedulesdirect.org/stationLogos/s56783_light_360w_270h.png?v=9a56b7b950fb8f7293d2"
OVERRIDE_URL = "https://raw.githubusercontent.com/tv-logo/tv-logos/main/countries/united-kingdom/hd/sky-sports-uhd-uk.png"

SNAPSHOT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "tvlogos_files.txt"
)
TL_SCREENPIX = (
    "https://raw.githubusercontent.com/tv-logo/tv-logos/main/"
    "countries/united-states/screenpix-action-us.png"
)


def snapshot_index():
    """The #369 tv-logos tree snapshot, indexed the way the plugin indexes the API."""
    with open(SNAPSHOT, encoding="utf-8") as handle:
        names = [line.strip() for line in handle if line.strip()]
    paths = [
        tvlogos.COUNTRY_PREFIX + name for name in names if name.lower().endswith(".png")
    ]
    return tvlogos.build_index(paths)


INDEX = snapshot_index()


def channel(number=526, name="Big Ten Network", current=None, station_id="56783"):
    return core.ChannelView(
        number=number, name=name, current_logo_url=current, station_id=station_id
    )


class OverrideTests(unittest.TestCase):
    def test_override_url_wins_over_sd_candidate(self):
        decision = core.decide_channel(
            channel(),
            "dark",
            {"526": OVERRIDE_URL},
            {"56783": DARK},
        )
        self.assertEqual(decision.decision, core.OVERRIDE)
        self.assertEqual(decision.source, core.SOURCE_OVERRIDE)
        self.assertEqual(decision.candidate_url, OVERRIDE_URL)

    def test_keep_override_wins_over_replace(self):
        decision = core.decide_channel(
            channel(), "dark", {"526": "keep"}, {"56783": DARK}
        )
        self.assertEqual(decision.decision, core.OVERRIDE)
        self.assertEqual(decision.candidate_url, "")
        self.assertIn("keep", decision.reason)

    def test_override_key_accepts_float_style_number(self):
        overrides = core.parse_overrides('{"526.0": "%s"}' % OVERRIDE_URL)
        decision = core.decide_channel(channel(), "dark", overrides, {"56783": DARK})
        self.assertEqual(decision.candidate_url, OVERRIDE_URL)

    def test_parse_overrides_normalizes_keys(self):
        overrides = core.parse_overrides('{"582": "https://example/logo.png", "600.0": "keep"}')
        self.assertEqual(
            overrides,
            {"582": "https://example/logo.png", "600": "keep"},
        )
        self.assertEqual(core.parse_overrides("{}"), {})
        self.assertEqual(core.parse_overrides(""), {})

    def test_parse_overrides_rejects_non_object_and_non_string(self):
        with self.assertRaises(ValueError):
            core.parse_overrides("[]")
        with self.assertRaises(ValueError):
            core.parse_overrides("{not json")


class StationIdTests(unittest.TestCase):
    def test_numeric_station_id_uses_sd_logo(self):
        decision = core.decide_channel(channel(station_id="56783"), "dark", {}, {"56783": DARK})
        self.assertEqual(decision.decision, core.REPLACE)
        self.assertEqual(decision.candidate_url, DARK)

    def test_non_numeric_station_id_is_no_match(self):
        decision = core.decide_channel(
            channel(station_id="BigTen.us"), "dark", {}, {"56783": DARK}
        )
        self.assertEqual(decision.decision, core.KEEP_NO_MATCH)
        self.assertEqual(decision.candidate_url, "")

    def test_missing_epg_data_is_no_match(self):
        decision = core.decide_channel(channel(station_id=None), "dark", {}, {"56783": DARK})
        self.assertEqual(decision.decision, core.KEEP_NO_MATCH)

    def test_numeric_station_id_without_dark_logo_is_no_match(self):
        decision = core.decide_channel(
            channel(station_id="32343"), "dark", {}, {"32343": LIGHT}
        )
        self.assertEqual(decision.decision, core.KEEP_NO_MATCH)


class StyleFilteringTests(unittest.TestCase):
    def test_light_sd_url_is_not_used_for_dark_style(self):
        decision = core.decide_channel(channel(), "dark", {}, {"56783": LIGHT})
        self.assertEqual(decision.decision, core.KEEP_NO_MATCH)

    def test_light_style_uses_light_url(self):
        decision = core.decide_channel(channel(), "light", {}, {"56783": DARK})
        self.assertEqual(decision.decision, core.KEEP_NO_MATCH)
        decision = core.decide_channel(channel(), "light", {}, {"56783": LIGHT})
        self.assertEqual(decision.decision, core.REPLACE)
        self.assertEqual(decision.candidate_url, LIGHT)

    def test_multiple_sd_sources_pick_the_matching_style(self):
        decision = core.decide_channel(
            channel(), "dark", {}, {"56783": [LIGHT, DARK]}
        )
        self.assertEqual(decision.candidate_url, DARK)


class IdempotencyTests(unittest.TestCase):
    def test_already_preferred_logo_is_kept(self):
        decision = core.decide_channel(channel(current=DARK), "dark", {}, {"56783": DARK})
        self.assertEqual(decision.decision, core.KEEP_ALREADY)
        self.assertEqual(core.summary([decision])["replace"], 0)

    def test_non_sd_logo_of_same_style_is_replaced(self):
        decision = core.decide_channel(
            channel(current="https://example.test/foo_dark_.png"),
            "dark",
            {},
            {"56783": DARK},
        )
        self.assertEqual(decision.decision, core.REPLACE)

    def test_second_apply_has_no_changes(self):
        first = core.decide_channel(channel(current=None), "dark", {}, {"56783": DARK})
        self.assertTrue(core.should_apply(first))
        # After apply the channel points at the candidate URL.
        second = core.decide_channel(channel(current=DARK), "dark", {}, {"56783": DARK})
        self.assertFalse(core.should_apply(second))
        self.assertEqual(second.decision, core.KEEP_ALREADY)


class ReportTests(unittest.TestCase):
    def test_summary_counts(self):
        decisions = [
            core.decide_channel(channel(number=1), "dark", {}, {"56783": DARK}),
            core.decide_channel(channel(number=2, current=DARK), "dark", {}, {"56783": DARK}),
            core.decide_channel(channel(number=3, station_id="abc"), "dark", {}, {}),
            core.decide_channel(channel(number=4), "dark", {"4": OVERRIDE_URL}, {}),
        ]
        counts = core.summary(decisions)
        self.assertEqual(counts["total"], 4)
        self.assertEqual(counts["replace"], 1)
        self.assertEqual(counts["keep_already_preferred"], 1)
        self.assertEqual(counts["keep_no_match"], 1)
        self.assertEqual(counts["override"], 1)
        self.assertEqual(counts["total"], sum(v for k, v in counts.items() if k != "total"))

    def test_report_row_shape(self):
        decision = core.decide_channel(channel(), "dark", {}, {"56783": DARK})
        row = core.report_rows([decision])[0]
        self.assertEqual(
            set(row),
            {
                "channel_number",
                "channel_name",
                "current_url",
                "candidate_url",
                "source",
                "decision",
                "reason",
            },
        )
        self.assertEqual(row["channel_number"], "526")

    def test_format_channel_number(self):
        self.assertEqual(core.format_channel_number(526.0), "526")
        self.assertEqual(core.format_channel_number(526), "526")
        self.assertEqual(core.format_channel_number(12.5), "12.5")
        self.assertEqual(core.format_channel_number(None), "")


class TvLogosSourceTests(unittest.TestCase):
    def view(self, name, station_id=None, current=None, number=661):
        return core.ChannelView(
            number=number, name=name, current_logo_url=current, station_id=station_id
        )

    def test_parse_sources_orders_and_drops_duplicates(self):
        self.assertEqual(core.parse_sources("tvlogos,sd"), ("sd", "tvlogos"))
        self.assertEqual(core.parse_sources(" tvlogos ; tvlogos "), ("tvlogos",))
        self.assertEqual(core.parse_sources(None), ("sd", "tvlogos"))
        self.assertEqual(core.parse_sources(""), ("sd", "tvlogos"))

    def test_parse_sources_rejects_unknown_names(self):
        with self.assertRaises(ValueError):
            core.parse_sources("sd,iptvorg")

    def test_sd_wins_over_tvlogos(self):
        decision = core.decide_channel(
            self.view("ScreenPix Action", "56783"), "dark", {}, {"56783": DARK}, INDEX
        )
        self.assertEqual(decision.decision, core.REPLACE)
        self.assertEqual(decision.source, core.SOURCE_SD)
        self.assertEqual(decision.candidate_url, DARK)

    def test_tvlogos_is_used_when_sd_has_nothing(self):
        decision = core.decide_channel(
            self.view("ScreenPix Action", "ScreenPixAction.us"), "dark", {}, {}, INDEX
        )
        self.assertEqual(decision.decision, core.REPLACE)
        self.assertEqual(decision.source, core.SOURCE_TVLOGOS)
        self.assertEqual(decision.candidate_url, TL_SCREENPIX)

    def test_sources_setting_can_disable_tvlogos(self):
        decision = core.decide_channel(
            self.view("ScreenPix Action", "ScreenPixAction.us"),
            "dark",
            {},
            {},
            INDEX,
            "sd",
        )
        self.assertEqual(decision.decision, core.KEEP_NO_MATCH)
        self.assertEqual(decision.candidate_url, "")
        self.assertIn("no numeric Gracenote station id", decision.reason)

    def test_sources_setting_can_disable_sd(self):
        decision = core.decide_channel(
            self.view("ScreenPix Action", "56783"), "dark", {}, {"56783": DARK}, INDEX, "tvlogos"
        )
        self.assertEqual(decision.decision, core.REPLACE)
        self.assertEqual(decision.source, core.SOURCE_TVLOGOS)

    def test_generic_only_keeps_the_current_logo(self):
        decision = core.decide_channel(
            self.view("ESPN 4K", "ESPN4K.us", "https://example.test/espn-4k.png"),
            "dark",
            {},
            {},
            INDEX,
        )
        self.assertEqual(decision.decision, core.KEEP_GENERIC)
        self.assertEqual(decision.candidate_url, "")
        self.assertFalse(core.should_apply(decision))
        self.assertIn("generic only", decision.reason)
        self.assertIn("espn-us.png", decision.reason)

    def test_big_ten_overflow_is_generic_only(self):
        decision = core.decide_channel(
            self.view("Big Ten Overflow 2", "BigTen2.us"), "dark", {}, {}, INDEX
        )
        self.assertEqual(decision.decision, core.KEEP_GENERIC)

    def test_tsn_team_feed_is_generic_only(self):
        decision = core.decide_channel(
            self.view("TSN 4K Toronto Maple Leafs", "TorontoMapleLeafs.nhl"),
            "dark",
            {},
            {},
            INDEX,
        )
        self.assertEqual(decision.decision, core.KEEP_GENERIC)
        self.assertIn("tsn-4k-ca.png", decision.reason)

    def test_already_preferred_tvlogos_url(self):
        decision = core.decide_channel(
            self.view("ScreenPix Action", "ScreenPixAction.us", TL_SCREENPIX),
            "dark",
            {},
            {},
            INDEX,
        )
        self.assertEqual(decision.decision, core.KEEP_ALREADY)
        self.assertEqual(decision.source, core.SOURCE_TVLOGOS)

    def test_second_tvlogos_apply_is_a_noop(self):
        first = core.decide_channel(
            self.view("ScreenPix Action", "ScreenPixAction.us"), "dark", {}, {}, INDEX
        )
        second = core.decide_channel(
            self.view("ScreenPix Action", "ScreenPixAction.us", first.candidate_url),
            "dark",
            {},
            {},
            INDEX,
        )
        self.assertFalse(core.should_apply(second))
        self.assertEqual(second.decision, core.KEEP_ALREADY)

    def test_provider_tvg_id_selects_the_country(self):
        view = core.ChannelView(
            number=583,
            name="Sky Sports Main Event 4K",
            station_id="49609",
            tvg_id="SkySportsMainEventUHD.uk",
        )
        decision = core.decide_channel(view, "dark", {}, {}, INDEX)
        self.assertEqual(decision.decision, core.REPLACE)
        self.assertEqual(
            decision.candidate_url,
            tvlogos.RAW_BASE
            + "countries/united-kingdom/hd/sky-sports-main-event-uhd-uk.png",
        )

    def test_default_country_is_used_without_a_tvg_suffix(self):
        decision = core.decide_channel(
            self.view("ScreenPix Action", None), "dark", {}, {}, INDEX
        )
        self.assertEqual(decision.decision, core.REPLACE)

    def test_summary_counts_generic_only(self):
        decisions = [
            core.decide_channel(self.view("ESPN 4K", "ESPN4K.us"), "dark", {}, {}, INDEX),
            core.decide_channel(
                self.view("ScreenPix Action", "ScreenPixAction.us"), "dark", {}, {}, INDEX
            ),
        ]
        counts = core.summary(decisions)
        self.assertEqual(counts["keep_generic"], 1)
        self.assertEqual(counts["replace"], 1)
        self.assertEqual(counts["total"], sum(v for k, v in counts.items() if k != "total"))


if __name__ == "__main__":
    unittest.main()
