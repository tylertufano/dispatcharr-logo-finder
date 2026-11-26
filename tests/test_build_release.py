import hashlib
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_release.py"


def load_build_release():
    spec = importlib.util.spec_from_file_location("build_release", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_release = load_build_release()


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ReproducibleBuildTests(unittest.TestCase):
    def test_rebuild_has_the_same_sha256(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = build_release.build_zip(Path(tmp) / "first.zip")
            second = build_release.build_zip(Path(tmp) / "second.zip")
            self.assertEqual(sha256(first), sha256(second))

    def test_zip_is_flat_and_has_no_tests_or_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = build_release.build_zip(Path(tmp) / "plugin.zip")
            with zipfile.ZipFile(archive_path) as archive:
                names = archive.namelist()

        self.assertIn("plugin.json", names)
        self.assertIn("plugin.py", names)
        for name in names:
            self.assertNotIn("/", name)
            self.assertFalse(name.startswith("tests/"))
            self.assertNotIn("__pycache__", name)
            self.assertFalse(name.endswith((".pyc", ".pyo")))

    def test_committed_manifests_match_a_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = build_release.build_zip(Path(tmp) / "plugin.zip")
            digest = sha256(archive_path)

        root_manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        entry = root_manifest["manifest"]["plugins"][0]
        self.assertEqual(entry["latest_sha256"], digest)

        per_plugin = json.loads(
            (ROOT / "zips" / build_release.SLUG / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(per_plugin["manifest"]["latest"]["checksum_sha256"], digest)
        self.assertEqual(
            per_plugin["manifest"]["latest"]["version"], entry["latest_version"]
        )

        plugin_json = json.loads(
            (ROOT / "src" / "logo_finder" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(plugin_json["version"], entry["latest_version"])

        plugin_source = (ROOT / "src" / "logo_finder" / "plugin.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(f'version = "{entry["latest_version"]}"', plugin_source)


if __name__ == "__main__":
    unittest.main()
