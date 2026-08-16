# soularr-fork

Fork of [mrusse/soularr](https://github.com/mrusse/soularr) (GPL-3.0) adding
archival-quality control for a music homelab: per-album-type quality ladders,
proof-gated (log+cue) album sourcing, single/album dedup, and safer searches.
Branch `homelab` = upstream `main` + fork features; image published as
`ghcr.io/hardstylejunkie/soularr-fork`.

This file is the fork's documentation. `README.md` is upstream's and churns on
sync merges — fork docs live here only.

## Features and config keys

All keys live in `config.ini`. New logic sits in the `soularr_fork/` package
(stdlib only); `soularr.py` calls into it.

### Per-album-type processing + quality ladders (F3)

`[Search Settings]`

- `processed_album_types` — Lidarr album types Soularr will process
  (`Album,EP,Single,Broadcast,Other`...). Empty = every type, as upstream.
- `album_allowed_filetypes` / `ep_allowed_filetypes` /
  `single_allowed_filetypes` — per-type quality ladder, same format as
  `allowed_filetypes`. Empty = inherit `allowed_filetypes`.

### Proof-gated album candidates (F1)

`[Search Settings]`

- `require_proof_album_types` — album types that must come from a folder
  containing every proof extension and no audio besides FLAC. Soularr iterates
  search candidates until one passes the gate. Empty = gate off.
- `proof_files` — extensions the folder must contain (default `log,cue`).
- `proof_accept_scene_names` — proof requirement is also satisfied by a folder
  whose leaf name parses (via the Lidarr parser port) to the wanted artist +
  album with an explicit FLAC token — scene/WEB FLAC releases have no log/cue
  by nature. Folder contents must still be all-FLAC. `False` = log+cue only.
  Default on (only matters for types listed in `require_proof_album_types`).
- `max_directory_probes` — cap on user directory fetches per album while
  hunting a proof-verified folder (default `30`).

### Skip singles already on studio albums (F4)

`[Search Settings]`

- `skip_singles_on_albums` — before grabbing a Single, match its track titles
  (Unicode-safe normalization: accent folding, feat./remaster stripping)
  against studio albums already in the library. Conservative: if the single
  has any track not covered by an album, it is still grabbed. Only albums the
  user monitors or already owns files for count toward the match
  (`require_album_presence`, on by default in `SingleDedup`) — metadata-only
  albums Lidarr merely knows about never cause a skip.

### Staged-download dedup

`[Download Settings]`

- `skip_already_staged` — skip albums whose renamed staging folder already
  exists in the download dir.
- `staged_memory_days` — also skip albums grabbed within the last N days
  (default `7`).

### Punctuation-safe search queries

`[Search Settings]`

- `sanitize_search_queries` — strip punctuation/noise (curly quotes included)
  that breaks Soulseek searches; if the sanitized query errors or returns
  nothing, the raw query is retried once. Off by default.

### Wanted-list processing order

`[Search Settings]`

- `search_sort_key` — how the Lidarr wanted list is ordered while paging:
  `albums.title` (upstream default, alphabetical by album title),
  `artists.sortname` (groups a whole artist together, so each artist is
  worked to completion before moving on — also maximizes the F4 dedup's
  per-artist cache hits), or `releasedate`. Invalid values warn and fall
  back to `albums.title`.

### Replace low-scoring copies (cf_below)

`[Search Settings]`

`search_source` now accepts a comma-separated list (`missing | cutoff_unmet |
all | e.g. missing,cf_below`; `all` still means missing+cutoff_unmet as
upstream). Adding `cf_below` makes Soularr hunt archival REPLACEMENT copies
for albums whose existing files carry a custom-format score below the
threshold — the user's LQ/vinyl-rip CF flags. Candidates flow through the
same pipeline as wanted albums, proof/scene gate included. Lidarr's own
RSS quality/score upgrade machinery is unaffected; this only feeds
Soularr's search list.

- `cf_below_threshold` — an album qualifies when the minimum of its files'
  `customFormatScore`s is STRICTLY below this (default `0`). Min-of-files
  aggregation means a partially bad copy (one flagged file among clean
  ones) qualifies. Files without a score are ignored, and an album with no
  scored files never qualifies — missing data is not treated as bad.
- `cf_artists_per_run` — artists inspected per run (default `10`). A rolling
  cursor (`.cf_artist_cursor.txt` in the var dir) walks the library in
  sortName order and wraps at the end, so the whole library gets swept
  across runs. Only monitored albums of type Album are considered.

With `disable_sync = True` the replacement lands in the staging folder for
manual import — deliberate: replacing existing files goes through your
review, not an automatic import. (Unless promote is on — see below.)

### Promote (auto-import, token naming)

`[Download Settings]`

- `promote_completed` — the fork's own auto-import for the `disable_sync =
  True` workflow (off = staging behaves exactly as today). Off by default.
- `recycle_bin` — where replaced old album folders are moved (default
  `/data/media/music/.RecycleBin`). Nothing is ever deleted.

The library's custom-format scores are driven by **folder-name tokens**:
`[LOG+CUE]` (+18), `[CD]`/`[WEB]`, the `FLAC` token, and a trailing `-GROUP`
release-group suffix (its absence scores No-RlsGroup). Lidarr's own
RenameFiles is therefore **never used** — it would strip those tokens.
Albums enter the library by moving a fully-named folder into the artist
directory and letting Lidarr map the files in place.

Promote automates exactly that. When a download completes it composes the
library convention:

```
{Artist} - {Title} ({Year})[ [Release Disambiguation]] [{Medium}][FLAC {Depth}bit][LOG+CUE][-GROUP]
```

- Medium comes from the chosen Lidarr release's `format` (`CD`/`Digital
  Media`→`WEB`/`Vinyl`), falling back to the source parsed from the original
  slskd folder name, then CD/WEB by proof presence.
- Depth is read from the first FLAC's STREAMINFO block (a bare `[FLAC]`
  token when unreadable). `[LOG+CUE]` appears when the folder holds both a
  `.log` and a `.cue`; the release group comes from parsing the original
  slskd folder name.

Flow: **move → refresh → verify → recycle**. The staged folder is moved into
the artist directory (created if missing) under the composed name, a
`RefreshArtist` command is posted and awaited, then the album's trackfiles
are re-fetched to verify at least one now lives inside the new folder. Only
after that verification are old folders the album no longer references moved
into `recycle_bin/replaced-<YYYYMMDD>/` — never deleted, and never touched if
still referenced.

Fail-open semantics: every error logs a warning and returns without crashing
the run loop. A failure before the move leaves the folder in staging; a
failure after it (refresh error, verify miss) leaves the folder in the artist
directory where the next root-folder rescan retries the mapping. Nothing
outside the recycle-bin move ever removes a folder.

Requirement: the download staging dir must live **outside** the Lidarr root
folder — `RefreshArtist` rescans the root, and a staging dir inside it would
get scanned mid-download.

## Homelab config example

Albums FLAC-only with log+cue proof; EPs and singles FLAC falling back to
MP3 320:

```ini
[Search Settings]
processed_album_types = Album,EP,Single
album_allowed_filetypes = flac 16/44.1,flac
ep_allowed_filetypes = flac,mp3 320
single_allowed_filetypes = flac,mp3 320
require_proof_album_types = Album
proof_files = log,cue
max_directory_probes = 30
skip_singles_on_albums = True
sanitize_search_queries = True
search_sort_key = artists.sortname

[Download Settings]
skip_already_staged = True
staged_memory_days = 7
promote_completed = True
```

## Upstream sync

CI (`.github/workflows/fork-build.yml`) merges `mrusse/soularr` `main` into
`homelab` weekly (and on manual dispatch); merge conflicts fail the run and
are resolved by hand. Every push/merge runs the test suite, then builds and
pushes `ghcr.io/hardstylejunkie/soularr-fork:latest` and `:run-<n>`.

## Compatibility

A config.ini without any fork keys behaves identically to upstream: every
fork feature defaults off (or to upstream behavior). The shipped example
`config.ini` turns `sanitize_search_queries` and `skip_already_staged` on
explicitly.

## Deployment notes

- The first ghcr publish creates the package **private** — flip it to public
  in the GitHub package settings before Unraid can pull anonymously.
- Upstream workflows (`docker.yaml`, `release-on-tag.yaml`, `spelling.yaml`)
  are disabled at the repo level via `gh` (no file edits, so they survive
  sync merges).
- The repo's default branch is `homelab`, so the weekly upstream-sync
  schedule in `fork-build.yml` actually fires (scheduled workflows only run
  from the default branch).
