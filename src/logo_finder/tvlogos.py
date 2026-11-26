"""tv-logos (``tv-logo/tv-logos``) source: one cached tree call, exact matches only.

The repository holds one PNG per channel at
``countries/<country>/<name>-<cc>.png``, with some ``hd/`` variants, US station
files in ``countries/united-states/us-local/`` and a few ``-light``/``-white``
files. Every image is drawn for a dark background, so tv-logos counts as a
"dark" source.

Matching is deliberately exact. The only relaxed step exists to *recognise* a
parent-network file so the plugin can refuse it: when the best file is the
channel name with a distinguishing token missing ("ESPN 4K" -> ``espn-us.png``),
the match is reported as ``generic`` and the caller keeps the current logo.

This module has no Django imports; the GitHub call is injected in tests.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Mapping, Optional, Sequence

TREE_URL = "https://api.github.com/repos/tv-logo/tv-logos/git/trees/main?recursive=1"
RAW_BASE = "https://raw.githubusercontent.com/tv-logo/tv-logos/main/"
RAW_MARKER = "/tv-logo/tv-logos/"
COUNTRY_PREFIX = "countries/"
CACHE_FILENAME = "tvlogos-tree.json"
CACHE_TTL_SECONDS = 24 * 60 * 60
HTTP_TIMEOUT_SECONDS = 20
USER_AGENT = "dispatcharr-logo-finder (+https://github.com/tylertufano/dispatcharr-logo-finder)"

MATCH_EXACT = "exact"
MATCH_GENERIC = "generic"

# Subfolders that may supply a name match. ``us-local`` is handled by call sign
# and ``obsolete``/vendor trees are ignored: they hold retired or third-party art.
NAME_SUBFOLDERS = ("", "hd")
LOCAL_FOLDER = "united-states"
LOCAL_SUBFOLDER = "us-local"
INTERNATIONAL_FOLDER = "international"

# Tokens that never distinguish one channel from another. Dropped from both the
# channel name and the file name before comparing.
NOISE_TOKENS = frozenset(
    {"network", "networks", "channel", "channels", "television", "tv", "the"}
)
# Quality tokens: 4K and UHD name the same thing; HD is a plain marker. HD is
# dropped, 4K/UHD is kept because a file without it is not a 4K logo.
QUALITY_ALIASES = {"4k": "uhd", "uhd": "uhd"}
DROPPED_TOKENS = frozenset({"hd", "fhd", "hdtv"})
STYLE_MARKERS = ("light", "white")

CALLSIGN_RE = re.compile(r"^[kw][a-z]{2,4}$")
COUNTRY_CODE_RE = re.compile(r"^[a-z]{2,4}$")
TOKEN_RE = re.compile(r"[a-z0-9]+")
# ``ESPN2`` and ``espn-2`` name the same channel.
LETTER_DIGIT_RE = re.compile(r"^([a-z]+)([0-9]+)$")

# Provider suffixes that are not the tv-logos folder code.
COUNTRY_ALIASES = {"gb": "uk", "usa": "us", "gbr": "uk"}


class TvLogosError(RuntimeError):
    """The tree could not be fetched and no usable cache exists."""


@dataclass(frozen=True)
class LogoFile:
    """One PNG in the tv-logos tree."""

    path: str
    folder: str
    subfolder: str
    name: str
    tokens: tuple
    cc: str
    marker: Optional[str]

    @property
    def url(self) -> str:
        return RAW_BASE + self.path


@dataclass(frozen=True)
class TvLogosMatch:
    """A file that matches a channel, and whether it is the channel or its parent."""

    path: str
    url: str
    quality: str
    reason: str

    @property
    def is_exact(self) -> bool:
        return self.quality == MATCH_EXACT


@dataclass
class TvLogosIndex:
    """The parsed tree: files, folder/country maps and lookup tables."""

    files: tuple
    folder_cc: dict
    cc_folder: dict
    exact: dict
    us_local: dict
    by_folder: dict = field(default_factory=dict)

    @property
    def folders(self) -> tuple:
        return tuple(sorted(self.by_folder))

    def __len__(self) -> int:
        return len(self.files)


def parse_tree(payload) -> list:
    """Return the ``countries/**/*.png`` paths from a GitHub tree response."""
    tree = payload.get("tree") if isinstance(payload, Mapping) else None
    if not isinstance(tree, list):
        raise TvLogosError("unexpected tv-logos tree response: no 'tree' list")
    paths = []
    for entry in tree:
        if not isinstance(entry, Mapping) or entry.get("type") != "blob":
            continue
        path = entry.get("path") or ""
        if not isinstance(path, str):
            continue
        if path.startswith(COUNTRY_PREFIX) and path.lower().endswith(".png"):
            paths.append(path)
    return sorted(set(paths))


def _cache_path(data_dir: str) -> str:
    return os.path.join(data_dir, CACHE_FILENAME)


def _read_cache(data_dir: str) -> Optional[dict]:
    try:
        with open(_cache_path(data_dir), encoding="utf-8") as handle:
            data = json.load(handle)
        paths = [path for path in data.get("paths") or [] if isinstance(path, str)]
        fetched_at = datetime.fromisoformat(data["fetched_at"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return {"paths": paths, "fetched_at": fetched_at}


def _write_cache(data_dir: str, paths: Sequence[str], fetched_at: datetime) -> None:
    os.makedirs(data_dir, exist_ok=True)
    target = _cache_path(data_dir)
    temporary = target + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump({"fetched_at": fetched_at.isoformat(), "paths": list(paths)}, handle)
        handle.write("\n")
    os.replace(temporary, target)


def _http_get_json(url: str) -> dict:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_tree(
    data_dir: Optional[str],
    now: Optional[datetime] = None,
    get_json=None,
    ttl: int = CACHE_TTL_SECONDS,
) -> dict:
    """Return the tree paths, one API call per 24 h, with a stale-cache fallback.

    Returns ``{"paths", "cache_age_seconds", "from_cache", "stale"}`` and, when
    the API failed and a stale cache was used, ``"error"``. Raises
    :class:`TvLogosError` when there is neither a call nor a cache.
    """
    now = now or datetime.now(timezone.utc)
    cached = _read_cache(data_dir) if data_dir else None
    age = (now - cached["fetched_at"]).total_seconds() if cached else None

    if cached and age is not None and age < ttl:
        return {
            "paths": cached["paths"],
            "cache_age_seconds": age,
            "from_cache": True,
            "stale": False,
        }

    try:
        payload = (get_json or _http_get_json)(TREE_URL)
        paths = parse_tree(payload)
        if not paths:
            raise TvLogosError("the tv-logos tree response had no PNG paths")
        if data_dir:
            _write_cache(data_dir, paths, now)
        return {
            "paths": paths,
            "cache_age_seconds": 0.0,
            "from_cache": False,
            "stale": False,
        }
    except Exception as exc:  # noqa: BLE001 - any failure falls back to the cache
        if cached:
            return {
                "paths": cached["paths"],
                "cache_age_seconds": age,
                "from_cache": True,
                "stale": True,
                "error": str(exc),
            }
        raise TvLogosError(f"could not fetch the tv-logos tree: {exc}") from exc


def _split_token(token: str) -> tuple:
    """Split ``espn2`` into ``("espn", "2")``; leave ``4k`` and plain words alone."""
    match = LETTER_DIGIT_RE.match(token)
    return (match.group(1), match.group(2)) if match else (token,)


def _clean_tokens(raw_tokens: Iterable[str]) -> tuple:
    """Drop style/quality/noise tokens and record the style marker."""
    tokens = []
    marker = None
    for token in raw_tokens:
        if token in STYLE_MARKERS:
            marker = marker or token
            continue
        if token in DROPPED_TOKENS or token in NOISE_TOKENS:
            continue
        tokens.append(QUALITY_ALIASES.get(token, token))
    return tuple(tokens), marker


def normalize(name: str) -> tuple:
    """Turn a channel or file name into comparable tokens.

    Lowercase, ``&`` -> ``and``, ``+`` -> ``plus``, punctuation splits tokens,
    ``hd``/noise tokens are dropped, ``4k`` becomes ``uhd`` and ``ESPN2``
    becomes ``espn 2``.
    """
    text = (name or "").lower()
    text = text.replace("&", " and ").replace("+", " plus ")
    raw = []
    for token in TOKEN_RE.findall(text):
        raw.extend(_split_token(token))
    tokens, _marker = _clean_tokens(raw)
    return tokens


def compact(tokens: Iterable[str]) -> str:
    """Join tokens so ``ViendoMovies`` and ``viendo-movies`` compare equal."""
    return "".join(tokens)


def split_stem(stem: str) -> tuple:
    """Split a file stem into ``(raw tokens, country code)``."""
    raw = TOKEN_RE.findall((stem or "").lower())
    cc = ""
    if len(raw) > 1 and COUNTRY_CODE_RE.match(raw[-1]):
        cc = raw[-1]
        raw = raw[:-1]
    return tuple(raw), cc


def call_sign(name: str) -> Optional[str]:
    """Return the first US call sign (``WBBH`` in ``WBBH NBC 2``), if any."""
    for token in TOKEN_RE.findall((name or "").lower()):
        if CALLSIGN_RE.match(token):
            return token
    return None


def strip_call_sign(name: str) -> str:
    """Remove the call sign token so the rest can match a network file."""
    sign = call_sign(name)
    if not sign:
        return name or ""
    kept = [token for token in (name or "").split() if token.lower() != sign]
    return " ".join(kept)


def country_code(tvg_id: Optional[str]) -> Optional[str]:
    """Country code from a provider id suffix (``BBCWorldNews.us`` -> ``us``)."""
    if not isinstance(tvg_id, str) or "." not in tvg_id:
        return None
    suffix = tvg_id.rsplit(".", 1)[1].strip().lower()
    if not COUNTRY_CODE_RE.match(suffix):
        return None
    return COUNTRY_ALIASES.get(suffix, suffix)


def _logo_file(path: str) -> Optional[LogoFile]:
    relative = path[len(COUNTRY_PREFIX) :]
    parts = relative.split("/")
    if len(parts) < 2 or not parts[-1]:
        return None
    folder = parts[0]
    subfolder = "" if len(parts) == 2 else "/".join(parts[1:-1])
    raw_tokens, cc = split_stem(parts[-1][:-4])
    expanded = []
    for token in raw_tokens:
        expanded.extend(_split_token(token))
    name_tokens, marker = _clean_tokens(expanded)
    return LogoFile(
        path=path,
        folder=folder,
        subfolder=subfolder,
        name=compact(name_tokens),
        tokens=name_tokens,
        cc=cc,
        marker=marker,
    )


def build_index(paths: Iterable[str]) -> TvLogosIndex:
    """Index the PNG paths: exact names per folder, US station files per call sign."""
    files = []
    for path in paths:
        if not isinstance(path, str) or not path.startswith(COUNTRY_PREFIX):
            continue
        if not path.lower().endswith(".png"):
            continue
        logo = _logo_file(path)
        if logo is not None:
            files.append(logo)
    files.sort(key=lambda logo: logo.path)

    counts = {}
    for logo in files:
        if not logo.cc or logo.subfolder not in NAME_SUBFOLDERS:
            continue
        counts.setdefault(logo.folder, {})
        counts[logo.folder][logo.cc] = counts[logo.folder].get(logo.cc, 0) + 1
    folder_cc = {
        folder: max(sorted(ccs.items()), key=lambda item: item[1])[0]
        for folder, ccs in counts.items()
    }
    cc_folder = {}
    for folder in sorted(folder_cc):
        cc_folder.setdefault(folder_cc[folder], folder)

    exact = {}
    us_local = {}
    by_folder = {}
    for logo in files:
        if logo.folder == LOCAL_FOLDER and logo.subfolder == LOCAL_SUBFOLDER:
            for token in logo.tokens:
                if CALLSIGN_RE.match(token):
                    us_local.setdefault(token, []).append(logo)
            continue
        if logo.subfolder not in NAME_SUBFOLDERS or not logo.name:
            continue
        exact.setdefault((logo.folder, logo.name), []).append(logo)
        by_folder.setdefault(logo.folder, []).append(logo)
    for bucket in exact.values():
        bucket.sort(key=lambda logo: (logo.subfolder != "", logo.path))

    return TvLogosIndex(
        files=tuple(files),
        folder_cc=folder_cc,
        cc_folder=cc_folder,
        exact=exact,
        us_local=us_local,
        by_folder=by_folder,
    )


def style_eligible(logo: LogoFile, style: str) -> bool:
    """Light/white art only for those styles; every other file is dark-background art."""
    style = (style or "dark").strip().lower()
    if style in STYLE_MARKERS:
        return logo.marker == style
    return logo.marker is None


def _pick(candidates: Iterable[LogoFile], style: str) -> Optional[LogoFile]:
    for logo in candidates:
        if style_eligible(logo, style):
            return logo
    return None


def _subsequence_leftover(file_tokens: Sequence[str], channel_tokens: Sequence[str]):
    """Channel tokens left over when the file name appears in order, else None."""
    position = 0
    leftover = []
    for token in channel_tokens:
        if position < len(file_tokens) and token == file_tokens[position]:
            position += 1
        else:
            leftover.append(token)
    if position != len(file_tokens):
        return None
    return leftover


def _generic_match(index: TvLogosIndex, folder: str, tokens: Sequence[str], style: str):
    """Best parent-network file in one folder, with the distinguishing tokens it lacks."""
    best = None
    best_score = (0, 0)
    best_leftover = ()
    for logo in index.by_folder.get(folder, ()):
        if not style_eligible(logo, style):
            continue
        if not logo.tokens or logo.tokens[0] != tokens[0]:
            continue
        leftover = _subsequence_leftover(logo.tokens, tokens)
        if not leftover:
            continue
        score = (len(logo.tokens), len(logo.name))
        if score > best_score:
            best, best_score, best_leftover = logo, score, tuple(leftover)
    return best, best_leftover


def match(
    index: TvLogosIndex,
    name: str,
    tvg_id: Optional[str] = None,
    style: str = "dark",
    default_country: str = "us",
) -> Optional[TvLogosMatch]:
    """Match one channel: call sign, then exact name, then the generic guard."""
    exact_tokens = normalize(name)
    if not exact_tokens:
        return None

    code = country_code(tvg_id) or (default_country or "us").strip().lower()
    code = COUNTRY_ALIASES.get(code, code)
    country_folder = index.cc_folder.get(code)
    folders = []
    if country_folder:
        folders.append(country_folder)
    if INTERNATIONAL_FOLDER not in folders:
        folders.append(INTERNATIONAL_FOLDER)

    sign = call_sign(name)
    if sign and country_folder == LOCAL_FOLDER:
        for logo in index.us_local.get(sign, ()):
            if style_eligible(logo, style):
                return TvLogosMatch(
                    logo.path,
                    logo.url,
                    MATCH_EXACT,
                    f"tv-logos station file for call sign {sign.upper()}",
                )

    key = compact(exact_tokens)
    for folder in folders:
        logo = _pick(index.exact.get((folder, key), ()), style)
        if logo is not None:
            return TvLogosMatch(logo.path, logo.url, MATCH_EXACT, "tv-logos exact name")

    relaxed_tokens = normalize(strip_call_sign(name)) or exact_tokens
    order = list(folders)
    order.extend(folder for folder in index.folders if folder not in order)
    for folder in order:
        logo, missing = _generic_match(index, folder, relaxed_tokens, style)
        if logo is not None:
            return TvLogosMatch(
                logo.path,
                logo.url,
                MATCH_GENERIC,
                f"tv-logos only has the parent network {logo.path}"
                f" (missing {' '.join(missing)!r})",
            )
    return None


def tvlogos_path(url: Optional[str]) -> Optional[str]:
    """Return the repo-relative path of a tv-logos raw URL, else None."""
    if not url or RAW_MARKER not in url:
        return None
    tail = url.split(RAW_MARKER, 1)[1]
    if "main/" not in tail:
        return None
    return tail.split("main/", 1)[1] or None


def is_preferred_url(url: Optional[str], name: str, index: TvLogosIndex) -> bool:
    """True when ``url`` already is a non-light tv-logos file for this channel."""
    path = tvlogos_path(url)
    if not path:
        return False
    if not path.startswith(COUNTRY_PREFIX):
        path = COUNTRY_PREFIX + path
    logo = _logo_file(path)
    if logo is None or logo.marker is not None or not logo.name:
        return False
    exact_tokens = normalize(name)
    if not exact_tokens:
        return False
    relaxed_tokens = normalize(strip_call_sign(name)) or exact_tokens
    return logo.name in {compact(exact_tokens), compact(relaxed_tokens)}
