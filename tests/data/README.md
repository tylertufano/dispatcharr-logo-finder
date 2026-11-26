# Test data

`tvlogos_files.txt` is a snapshot of the `tv-logo/tv-logos` repository file
list, one path per line, relative to the repo's `countries/` directory. It was
taken for komodo#369 (2026-11-25) and lets the tv-logos tests run without
network access. `tests/test_tvlogos.py` prefixes each line with `countries/`
so it is indexed exactly like a GitHub tree response.

Refresh it by replacing the file with the sorted `path` values of
`GET https://api.github.com/repos/tv-logo/tv-logos/git/trees/main?recursive=1`,
stripped of the leading `countries/`.
