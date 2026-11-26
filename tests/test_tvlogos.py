import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "logo_finder")
)

import tvlogos  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SNAPSHOT = os.path.join(DATA_DIR, "tvlogos_files.txt")

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)


def snapshot_paths():
    """The #369 tree snapshot, which lists paths relative to ``countries/``."""
    with open(SNAPSHOT, encoding="utf-8") as handle:
        names = [line.strip() for line in handle if line.strip()]
    return [
        tvlogos.COUNTRY_PREFIX + name for name in names if name.lower().endswith(".png")
    ]


def snapshot_index():
    return tvlogos.build_index(snapshot_paths())


class NormalizeTests(unittest.TestCase):
    def test_ampersand_and_plus_spell_out(self):
        self.assertEqual(tvlogos.normalize("MGM+ Drive-In"), ("mgm", "plus", "drive", "in"))
        self.assertEqual(tvlogos.normalize("A&E"), ("a", "and", "e"))

    def test_hd_and_noise_tokens_are_dropped(self):
        self.assertEqual(tvlogos.normalize("BBC World News HD"), ("bbc", "world", "news"))
        self.assertEqual(tvlogos.normalize("Big Ten Network"), ("big", "ten"))

    def test_four_k_and_uhd_are_the_same_token(self):
        self.assertEqual(tvlogos.normalize("NOW Sports 4K"), ("now", "sports", "uhd"))
        self.assertEqual(tvlogos.normalize("NOW Sports UHD"), ("now", "sports", "uhd"))

    def test_compact_ignores_word_boundaries(self):
        self.assertEqual(
            tvlogos.compact(tvlogos.normalize("ViendoMovies")),
            tvlogos.compact(tvlogos.normalize("viendo-movies")),
        )

    def test_letter_digit_names_split_into_tokens(self):
        self.assertEqual(tvlogos.normalize("ESPN2"), ("espn", "2"))
        self.assertEqual(tvlogos.normalize("TSN 4K"), ("tsn", "uhd"))
        self.assertEqual(
            tvlogos.compact(tvlogos.normalize("ESPN2")),
            tvlogos.compact(tvlogos.normalize("espn-2")),
        )

    def test_split_stem_takes_the_country_code(self):
        self.assertEqual(tvlogos.split_stem("nbc-2-wbbh-us"), (("nbc", "2", "wbbh"), "us"))
        self.assertEqual(tvlogos.split_stem("espn-us"), (("espn",), "us"))
        self.assertEqual(tvlogos.split_stem("v-sport-nordic"), (("v", "sport", "nordic"), ""))

    def test_call_sign_extraction(self):
        self.assertEqual(tvlogos.call_sign("WBBH NBC 2"), "wbbh")
        self.assertEqual(tvlogos.call_sign("WINK CBS 11"), "wink")
        self.assertIsNone(tvlogos.call_sign("ScreenPix Action"))
        self.assertIsNone(tvlogos.call_sign("Big Ten Network"))

    def test_strip_call_sign(self):
        self.assertEqual(tvlogos.strip_call_sign("WBBH NBC 2"), "NBC 2")
        self.assertEqual(tvlogos.strip_call_sign("Big Ten Network"), "Big Ten Network")

    def test_country_code_from_tvg_id(self):
        self.assertEqual(tvlogos.country_code("BBCWorldNews.us"), "us")
        self.assertEqual(tvlogos.country_code("SkySportsUltraHD.uk"), "uk")
        self.assertEqual(tvlogos.country_code("BBCWorldNews.gb"), "uk")
        self.assertEqual(tvlogos.country_code("TorontoMapleLeafs.nhl"), "nhl")
        self.assertIsNone(tvlogos.country_code("32343"))
        self.assertIsNone(tvlogos.country_code(None))


class TreeTests(unittest.TestCase):
    def test_parse_tree_keeps_country_pngs_only(self):
        payload = {
            "tree": [
                {"type": "blob", "path": "README.md"},
                {"type": "blob", "path": "countries/canada/tsn-ca.png"},
                {"type": "tree", "path": "countries/canada"},
                {"type": "blob", "path": "countries/canada/tsn-ca.svg"},
                {"type": "blob", "path": "countries/canada/0_all_logos_mosaic.md"},
            ]
        }
        self.assertEqual(tvlogos.parse_tree(payload), ["countries/canada/tsn-ca.png"])

    def test_parse_tree_rejects_a_non_tree_payload(self):
        with self.assertRaises(tvlogos.TvLogosError):
            tvlogos.parse_tree({"message": "API rate limit exceeded"})

    def test_index_maps_folder_codes_both_ways(self):
        index = snapshot_index()
        self.assertEqual(index.folder_cc["united-states"], "us")
        self.assertEqual(index.folder_cc["international"], "int")
        self.assertEqual(index.folder_cc["world-latin-america"], "lam")
        self.assertEqual(index.cc_folder["us"], "united-states")
        self.assertEqual(index.cc_folder["ca"], "canada")

    def test_index_ignores_obsolete_and_vendor_trees(self):
        index = snapshot_index()
        for folder, logos in index.by_folder.items():
            for logo in logos:
                self.assertIn(logo.subfolder, ("", "hd"), logo.path)
        self.assertNotIn(
            "countries/united-kingdom/obsolete/sony-channel-light-hz-uk.png",
            {logo.path for logos in index.exact.values() for logo in logos},
        )


class CacheTests(unittest.TestCase):
    def _payload(self):
        return {"tree": [{"type": "blob", "path": "countries/canada/tsn-ca.png"}]}

    def test_fresh_cache_skips_the_api(self):
        calls = []

        def get_json(url):
            calls.append(url)
            return self._payload()

        with tempfile.TemporaryDirectory() as data_dir:
            first = tvlogos.fetch_tree(data_dir, now=NOW, get_json=get_json)
            self.assertEqual(len(calls), 1)
            self.assertFalse(first["from_cache"])
            self.assertEqual(first["cache_age_seconds"], 0.0)

            def failing(url):
                raise AssertionError("the API must not be called while the cache is fresh")

            second = tvlogos.fetch_tree(
                data_dir, now=NOW + timedelta(hours=1), get_json=failing
            )
            self.assertTrue(second["from_cache"])
            self.assertFalse(second["stale"])
            self.assertAlmostEqual(second["cache_age_seconds"], 3600.0)
            self.assertEqual(second["paths"], first["paths"])

    def test_api_failure_falls_back_to_the_stale_cache(self):
        with tempfile.TemporaryDirectory() as data_dir:
            tvlogos.fetch_tree(data_dir, now=NOW, get_json=lambda url: self._payload())

            def failing(url):
                raise OSError("rate limit exceeded")

            result = tvlogos.fetch_tree(
                data_dir, now=NOW + timedelta(hours=25), get_json=failing
            )
            self.assertTrue(result["from_cache"])
            self.assertTrue(result["stale"])
            self.assertAlmostEqual(result["cache_age_seconds"], 25 * 3600.0)
            self.assertIn("rate limit", result["error"])
            self.assertEqual(result["paths"], ["countries/canada/tsn-ca.png"])

    def test_no_cache_and_api_failure_raises(self):
        with tempfile.TemporaryDirectory() as data_dir:
            with self.assertRaises(tvlogos.TvLogosError):
                tvlogos.fetch_tree(
                    data_dir,
                    now=NOW,
                    get_json=lambda url: (_ for _ in ()).throw(OSError("offline")),
                )

    def test_corrupt_cache_is_ignored(self):
        with tempfile.TemporaryDirectory() as data_dir:
            with open(os.path.join(data_dir, tvlogos.CACHE_FILENAME), "w") as handle:
                handle.write("{not json")
            result = tvlogos.fetch_tree(
                data_dir, now=NOW, get_json=lambda url: self._payload()
            )
            self.assertFalse(result["from_cache"])


class MatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = snapshot_index()

    def match(self, name, tvg_id=None, style="dark"):
        return tvlogos.match(self.index, name, tvg_id, style)

    def test_us_local_matches_on_call_sign(self):
        result = self.match("WBBH NBC 2", "NBCWBBH.us")
        self.assertTrue(result.is_exact)
        self.assertEqual(result.path, "countries/united-states/us-local/nbc-2-wbbh-us.png")

    def test_us_local_matches_when_the_call_sign_has_no_station_prefix(self):
        result = self.match("WINK CBS 11", "CBSWINK.us")
        self.assertEqual(result.path, "countries/united-states/us-local/wink-us.png")

    def test_bbc_world_news_uses_the_international_file(self):
        result = self.match("BBC World News", "BBCWorldNews.us")
        self.assertTrue(result.is_exact)
        self.assertEqual(result.path, "countries/international/bbc-world-news-int.png")

    def test_compact_name_matches_a_split_file_name(self):
        result = self.match("ViendoMovies", "ViendoMovies.us")
        self.assertTrue(result.is_exact)
        self.assertEqual(result.path, "countries/united-states/viendo-movies-us.png")

    def test_light_file_is_skipped_for_the_dark_style(self):
        result = self.match("ViendoMovies", "ViendoMovies.us")
        self.assertNotIn("light", result.path)
        light = self.match("ViendoMovies", "ViendoMovies.us", style="light")
        self.assertEqual(light.path, "countries/united-states/viendo-movies-light-us.png")

    def test_exact_multiword_name(self):
        result = self.match("ScreenPix Action", "ScreenPixAction.us")
        self.assertEqual(result.path, "countries/united-states/screenpix-action-us.png")

    def test_hd_subfolder_is_used_for_an_exact_name(self):
        result = self.match("Sky Sports Main Event 4K", "SkySportsMainEventUHD.uk")
        self.assertTrue(result.is_exact)
        self.assertEqual(
            result.path, "countries/united-kingdom/hd/sky-sports-main-event-uhd-uk.png"
        )

    def test_four_k_file_is_an_exact_match(self):
        result = self.match("NOW Sports 4K", "NowSports4K.hk")
        self.assertTrue(result.is_exact)
        self.assertEqual(result.path, "countries/hong-kong/now-sports-4k-hk.png")

    def test_missing_four_k_is_generic(self):
        result = self.match("ESPN 4K", "ESPN4K.us")
        self.assertFalse(result.is_exact)
        self.assertEqual(result.path, "countries/united-states/espn-us.png")
        self.assertIn("uhd", result.reason)

    def test_missing_overflow_number_is_generic(self):
        result = self.match("Big Ten Overflow 2", "BigTen2.us")
        self.assertFalse(result.is_exact)
        self.assertEqual(result.path, "countries/united-states/big-ten-network-us.png")

    def test_missing_team_name_is_generic(self):
        result = self.match("TSN 4K Toronto Maple Leafs", "TorontoMapleLeafs.nhl")
        self.assertFalse(result.is_exact)
        self.assertEqual(result.path, "countries/canada/tsn-4k-ca.png")

    def test_numbered_network_is_generic(self):
        result = self.match("ESPN2 Caribbean", "ESPN2.bs")
        self.assertFalse(result.is_exact)
        self.assertEqual(result.path, "countries/international/espn-int.png")

    def test_parent_network_is_generic(self):
        result = self.match("Sky Sports 1 4K", "SkySportsUltraHD.uk")
        self.assertFalse(result.is_exact)
        self.assertIn(result.path, {
            "countries/united-kingdom/hd/sky-sports-uhd-uk.png",
            "countries/united-kingdom/hd/sky-sports-main-event-uhd-uk.png",
        })

    def test_unknown_country_falls_back_to_the_default(self):
        result = self.match("BBC World News", "BBCWorldNews.us", style="dark")
        self.assertEqual(result.path, "countries/international/bbc-world-news-int.png")

    def test_no_file_is_no_match(self):
        self.assertIsNone(self.match("Zona Futbol", "ZONFUT"))


class PreferredUrlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = snapshot_index()

    def test_exact_tvlogos_url_is_preferred(self):
        url = tvlogos.RAW_BASE + "countries/united-states/screenpix-action-us.png"
        self.assertTrue(tvlogos.is_preferred_url(url, "ScreenPix Action", self.index))

    def test_light_tvlogos_url_is_not_preferred(self):
        url = tvlogos.RAW_BASE + "countries/united-states/viendo-movies-light-us.png"
        self.assertFalse(tvlogos.is_preferred_url(url, "ViendoMovies", self.index))

    def test_generic_tvlogos_url_is_not_preferred(self):
        url = tvlogos.RAW_BASE + "countries/united-states/espn-us.png"
        self.assertFalse(tvlogos.is_preferred_url(url, "ESPN 4K", self.index))

    def test_other_host_is_not_preferred(self):
        self.assertFalse(
            tvlogos.is_preferred_url("https://example.test/logo.png", "ESPN 4K", self.index)
        )


if __name__ == "__main__":
    unittest.main()
