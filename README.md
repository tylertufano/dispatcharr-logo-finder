# dispatcharr-logo-finder

Dispatcharr plugin: find channel logos in a preferred style (Schedules Direct
listed logos, tv-logos, iptv-org) and add them to the logo inventory.

**Status:** increment 2 of 4. This version uses **Schedules Direct** and
**tv-logos** (`tv-logo/tv-logos`). iptv-org and packaging arrive in later
increments.

## What it does

For every channel in the chosen channel profile it proposes one of:

| Decision | Meaning |
|---|---|
| `replace` | a logo of the chosen style exists in an enabled source |
| `override` | the channel number has an entry in the `overrides` setting |
| `keep: already preferred` | the current logo already is a logo of the chosen style from the first source that has one |
| `keep: generic only` | tv-logos only has the parent network (for example `espn-us.png` for "ESPN 4K"), so the current logo is kept |
| `keep: no match` | no logo of that style in any enabled source |

It never deletes a logo and never assigns a generic logo. `preview` writes the
report only; `apply` writes the report and assigns the logos in one
transaction. Running `apply` twice reports zero changes the second time.

### Sources and order

Sources run in this order: `overrides` → `sd` (Schedules Direct) → `tvlogos`.
The `sources` setting chooses which of `sd` and `tvlogos` are enabled; the
order is always the one above, whatever order the setting lists.

Style matching for Schedules Direct is substring based: an SD URL counts as
`dark` only when it contains `_dark_` (and likewise `_light_`, `_white_`,
`_gray_`). Schedules Direct stores each station's logo in the EPG source's
`logo_style`, keyed by the Gracenote station id in `EPGData.tvg_id`.

## tv-logos source

`tv-logos` has one dark-background PNG per channel plus occasional `hd/`
variants and `-light`/`-white` files. Every image is drawn for a dark
background, so tv-logos counts as a **dark** source: for the `dark` and `gray`
styles only files without a `-light`/`-white` marker are used, and for the
`light`/`white` styles only files with that marker are used.

**Tree and cache.** One unauthenticated call to
`GET https://api.github.com/repos/tv-logo/tv-logos/git/trees/main?recursive=1`
lists the whole repository. The PNG paths under `countries/` are cached in the
plugin data directory as `tvlogos-tree.json` for 24 hours, so a normal run makes
no request. The API allows 60 requests an hour unauthenticated; when the call
fails and a cache exists the plugin uses the stale cache and reports its age
(both in the action message and in the `tvlogos` field of the result). With no
cache and no API the plugin logs the error, continues with Schedules Direct,
and says so in the message.

**Paths and URLs.** The image URL is
`https://raw.githubusercontent.com/tv-logo/tv-logos/main/<path>`. Only the
country root, `hd/` and `united-states/us-local/` are used; `obsolete/` and
vendor subfolders are ignored.

**Matching (exact only).**

1. **US locals by call sign.** A token like `WBBH` in "WBBH NBC 2" is looked up
   in `countries/united-states/us-local/*-<callsign>-us.png`.
2. **Exact name.** The channel name and the file name are normalised
   (lowercase, `&` → `and`, `+` → `plus`, punctuation splits words, `ESPN2` →
   `espn 2`, `hd` dropped, `4k` ≡ `uhd`, and `network`/`channel`/`tv`/`the`
   ignored) and compared. Word boundaries do not matter, so `ViendoMovies`
   matches `viendo-movies-us.png`. The search order is the channel's country
   folder (root, then `hd/`), then `countries/international/`.
3. **Country.** The country comes from the `tvg_id` suffix (`.us`, `.uk`,
   `.ca`, `.hk`, ...; `.gb` counts as `uk`), else the `default_country`
   setting. The folder for a country code is read from the tree itself, so new
   tv-logos countries need no code change.

**Generic guard.** If no exact match exists, the plugin looks for the best file
whose name appears in order inside the channel name. When that file is missing
a distinguishing token of the channel name (`overflow 2`, `4k`/`uhd`, a team
name, `caribbean`, ...), the match is the parent network, not the channel:
the row is reported as `keep: generic only` and the current logo is kept. This
is why "ESPN 4K", "Big Ten Overflow 2" and "TSN 4K Toronto Maple Leafs" never
receive `espn-us.png`, `big-ten-network-us.png` or `tsn-4k-ca.png`.

**Already preferred.** A current logo is kept as `keep: already preferred`
when it is the same tv-logos URL as the candidate, or any non-light
`tv-logo/tv-logos` raw URL whose name is an exact match for the channel.

## Install

1. Copy `src/logo_finder/` into Dispatcharr's plugin directory (default
   `/app/data/plugins/logo_finder/`, or the path in `DISPATCHARR_PLUGINS_DIR`),
   or import it as a zip from the Plugins page.
2. Reload plugins and enable **Logo Finder**. The manifest is version 2 and
   requests the `background_tasks` and `outbound_network` capabilities.
3. Set the channel profile, style and sources, then run **Preview** before
   **Apply**.

## Settings

| Setting | Default | Notes |
|---|---|---|
| `profile` | `fixed` | Channel profile whose channels are considered. |
| `style` | `dark` | `dark`, `light`, `white` or `gray`. |
| `sources` | `sd,tvlogos` | Comma-separated sources: `sd`, `tvlogos`. Overrides always run first. |
| `default_country` | `us` | Two-letter country code used when the channel's `tvg_id` has no country suffix. |
| `overrides` | `{}` | JSON object keyed by channel number. A URL forces that logo; `"keep"` leaves the channel alone. Overrides win over every source. |

Example overrides (the planner sets these in the plugin settings after install;
they are not hard-coded here):

```json
{"582": "https://raw.githubusercontent.com/tv-logo/tv-logos/main/countries/united-kingdom/hd/sky-sports-uhd-uk.png"}
```

## Report files

Both actions write `logo-finder-<action>-<UTC timestamp>.json` and `.csv` into
the plugin's durable data directory. Dispatcharr exposes that directory to the
plugin as `context["data_dir"]`; for a managed plugin it is
`<plugins dir>/_data/<canonical plugin id>/` (for example
`/data/plugins/_data/logo-finder/`), and it survives plugin updates. See
`Plugins.md` → "`manifest_version` and `capabilities`" in the Dispatcharr
source. Each row has the channel number, name, current URL, candidate URL,
source (`sd`, `tvlogos` or `override`), decision and reason.

The action result also returns the summary counts, both report paths and, when
tv-logos is enabled, a `tvlogos` object with the indexed file count, the cache
age in hours and whether the cache was stale.

## Tests

The decision rules live in `src/logo_finder/core.py` and `tvlogos.py` and have
no Django imports. The tv-logos tests index
`tests/data/tvlogos_files.txt`, a snapshot of the repository tree taken for
komodo#369, so they need no network. Run the suite from the repository root:

```sh
python3 -m unittest discover -s tests -v
```
