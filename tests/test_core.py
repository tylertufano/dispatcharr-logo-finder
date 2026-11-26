import os
import sys
import unittest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "logo_finder")
)

import core  # noqa: E402

DARK = "https://logos.json.schedulesdirect.org/stationLogos/s56783_dark_360w_270h.png?v=9a56b7b950fb8f7293d2"
LIGHT = "https://logos.json.schedulesdirect.org/stationLogos/s56783_light_360w_270h.png?v=9a56b7b950fb8f7293d2"
OVERRIDE_URL = "https://raw.githubusercontent.com/tv-logo/tv-logos/main/countries/united-kingdom/hd/sky-sports-uhd-uk.png"


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


if __name__ == "__main__":
    unittest.main()
