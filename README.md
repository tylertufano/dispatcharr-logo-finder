# dispatcharr-logo-finder

Dispatcharr plugin: find channel logos in a preferred style (Schedules Direct
listed logos, tv-logos, iptv-org) and add them to the logo inventory.

**Status:** increment 1 of 4. This version uses the **Schedules Direct** source
only. tv-logos, iptv-org and packaging arrive in later increments.

## What it does

For every channel in the chosen channel profile it proposes one of:

| Decision | Meaning |
|---|---|
| `replace` | a Schedules Direct logo of the chosen style exists |
| `override` | the channel number has an entry in the `overrides` setting |
| `keep: already preferred` | the current logo already is a Schedules Direct logo of the chosen style |
| `keep: no match` | no Schedules Direct logo of that style, or no numeric Gracenote station id |

It never deletes a logo and never assigns a generic logo. `preview` writes the
report only; `apply` writes the report and assigns the logos in one
transaction. Running `apply` twice reports zero changes the second time.

Style matching is substring based in this increment: an SD URL counts as
`dark` only when it contains `_dark_` (and likewise `_light_`, `_white_`,
`_gray_`). Schedules Direct stores each station's logo in the EPG source's
`logo_style`, keyed by the Gracenote station id in `EPGData.tvg_id`.

## Install

1. Copy `src/logo_finder/` into Dispatcharr's plugin directory (default
   `/app/data/plugins/logo_finder/`, or the path in `DISPATCHARR_PLUGINS_DIR`),
   or import it as a zip from the Plugins page.
2. Reload plugins and enable **Logo Finder**. The manifest is version 2 and
   requests only the `background_tasks` capability.
3. Set the channel profile and style, then run **Preview** before **Apply**.

## Settings

| Setting | Default | Notes |
|---|---|---|
| `profile` | `fixed` | Channel profile whose channels are considered. |
| `style` | `dark` | `dark`, `light`, `white` or `gray`. |
| `overrides` | `{}` | JSON object keyed by channel number. A URL forces that logo; `"keep"` leaves the channel alone. Overrides win over Schedules Direct. |

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
source (`sd` or `override`), decision and reason.

The action result also returns the summary counts and both report paths.

## Tests

The decision rules live in `src/logo_finder/core.py` and have no Django
imports. Run the suite from the repository root:

```sh
python3 -m unittest discover -s tests -v
```
