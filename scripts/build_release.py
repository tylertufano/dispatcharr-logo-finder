#!/usr/bin/env python3
"""Build a reproducible Logo Finder release and its plugin-registry manifests.

Usage: python3 scripts/build_release.py VERSION
  e.g. python3 scripts/build_release.py 0.1.0

VERSION is stamped into ``src/logo_finder/plugin.json`` before the build, as
the Zap2XML fork's packaging script does. The release ZIP is flat: ``plugin.py``,
``plugin.json`` and the modules sit at the archive root, the layout Dispatcharr
installs under the plugin's slug directory. Entries are sorted and carry a fixed
timestamp, so rebuilding the same sources gives the same SHA-256.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path


OWNER = "tylertufano"
REPO = "dispatcharr-logo-finder"
BRANCH = "main"
SLUG = "logo-finder"
MIN_DISPATCHARR_VERSION = "0.34.0"
ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)
ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src" / "logo_finder"
RELEASE_URL = (
    f"https://github.com/{OWNER}/{REPO}/releases/download/v{{version}}/"
    f"{SLUG}-v{{version}}.zip"
)
EXCLUDED_DIRS = {"__pycache__", "tests"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def source_files(source: Path = SOURCE) -> list[Path]:
    """Return the plugin files in archive order, without caches or bytecode."""
    files = []
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.suffix in EXCLUDED_SUFFIXES or path.name.startswith("."):
            continue
        files.append(relative)
    return sorted(files, key=lambda relative: relative.as_posix())


def build_zip(destination: Path, source: Path = SOURCE) -> Path:
    """Write the flat, reproducible release ZIP and return its path."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        destination,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for relative in source_files(source):
            info = zipfile.ZipInfo(relative.as_posix(), date_time=ZIP_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.internal_attr = 0
            info.flag_bits = 0
            info.extra = b""
            info.comment = b""
            archive.writestr(
                info,
                (source / relative).read_bytes(),
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )
    return destination


def stamp_version(version: str, source: Path = SOURCE) -> dict:
    """Set the version in plugin.py and plugin.json, keeping their formatting."""
    plugin_json_path = source / "plugin.json"
    text = plugin_json_path.read_text(encoding="utf-8")
    text, replacements = re.subn(
        r'(?m)^(\s*"version"\s*:\s*)"[^"]*"',
        rf'\g<1>"{version}"',
        text,
        count=1,
    )
    if replacements != 1:
        raise SystemExit("plugin.json: no top-level version line found")
    plugin_json = json.loads(text)
    plugin_json_path.write_text(text, encoding="utf-8")

    plugin_py_path = source / "plugin.py"
    plugin_source = plugin_py_path.read_text(encoding="utf-8")
    plugin_source, replacements = re.subn(
        r'(?m)^(\s*version\s*=\s*)"[^"]*"',
        rf'\g<1>"{version}"',
        plugin_source,
        count=1,
    )
    if replacements != 1:
        raise SystemExit("plugin.py: no class version line found")
    plugin_py_path.write_text(plugin_source, encoding="utf-8")
    return plugin_json


def earlier_versions(manifest_path: Path, version: str) -> list[dict]:
    if not manifest_path.exists():
        return []
    old = json.loads(manifest_path.read_text(encoding="utf-8"))
    old_manifest = old.get("manifest", old)
    return [
        entry
        for entry in old_manifest.get("versions", [])
        if entry.get("version") != version
    ]


def update_manifests(version: str, plugin_json: dict, data: bytes) -> dict:
    """Write the root and per-plugin manifests for the built ZIP."""
    now = (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    url = RELEASE_URL.format(version=version)
    entry = {
        "version": version,
        "url": url,
        "checksum_md5": hashlib.md5(data).hexdigest(),
        "checksum_sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "last_updated": now,
        "build_timestamp": now,
        "min_dispatcharr_version": MIN_DISPATCHARR_VERSION,
    }
    description = plugin_json["description"]
    author = plugin_json["author"]
    registry_url = f"https://github.com/{OWNER}/{REPO}"

    per_plugin_path = ROOT / "zips" / SLUG / "manifest.json"
    versions = [entry] + earlier_versions(per_plugin_path, version)
    per_plugin = {
        "generated_at": now,
        "manifest": {
            "slug": SLUG,
            "name": plugin_json["name"],
            "description": description,
            "author": author,
            "registry_url": registry_url,
            "registry_name": f"{OWNER}/{REPO}",
            "last_updated": now,
            "latest": {**entry, "latest_url": url},
            "versions": versions,
        },
    }
    write_json(per_plugin_path, per_plugin)

    root_manifest = {
        "generated_at": now,
        "manifest": {
            "registry_url": registry_url,
            "registry_name": f"{OWNER}/{REPO}",
            "root_url": f"https://raw.githubusercontent.com/{OWNER}/{REPO}/{BRANCH}",
            "plugins": [
                {
                    "slug": SLUG,
                    "name": plugin_json["name"],
                    "description": description,
                    "manifest_url": f"zips/{SLUG}/manifest.json",
                    "author": author,
                    "last_updated": now,
                    "latest_version": version,
                    "latest_md5": entry["checksum_md5"],
                    "latest_sha256": entry["checksum_sha256"],
                    "latest_url": url,
                    "latest_size": entry["size"],
                    "help_url": plugin_json["help_url"],
                    "min_dispatcharr_version": MIN_DISPATCHARR_VERSION,
                }
            ],
        },
    }
    write_json(ROOT / "manifest.json", root_manifest)
    return entry


def main() -> None:
    if len(sys.argv) != 2 or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", sys.argv[1]):
        raise SystemExit("usage: build_release.py VERSION (three numeric parts, e.g. 0.1.0)")
    version = sys.argv[1]

    plugin_json = stamp_version(version)
    zip_path = build_zip(ROOT / "dist" / f"{SLUG}-v{version}.zip")
    data = zip_path.read_bytes()
    entry = update_manifests(version, plugin_json, data)
    print(
        f"{zip_path.name}: {entry['size']} bytes, "
        f"md5 {entry['checksum_md5']}, "
        f"sha256 {entry['checksum_sha256']}; "
        f"manifests updated"
    )


if __name__ == "__main__":
    main()
