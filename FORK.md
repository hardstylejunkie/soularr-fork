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

### Artist-first searches + search caps

`[Search Settings]`

A Soulseek search matches when every query word appears in a peer's file
path, and slskd closes a search at `response_limit` **peers** (one response =
one peer with all of its matching files), first come first served. Upstream
searches the bare album title, so "Icon" or "Greatest Hits" fills the cap
with other artists' folders. Measured on the homelab library (2026-09-28):
58 % of searches hit the 100-peer cap, the median capped search had 5 of 100
responders holding the wanted artist at all, and artist + title found FLAC
sources for 12 of 12 albums the bare title had missed.

- `album_prepend_artist` (upstream key) — search `artist title` instead of
  the bare title. The fork skips the prefix where it can only hurt: Lidarr's
  Various Artists entry, and titles that already spell the artist out
  (self-titled albums, "The Best of Toby Keith").
- `title_only_fallback` — when the artist + title query returns nobody
  (artist spelled differently in shares, phrases the Soulseek server refuses
  to search), retry with the bare title. Responses that merely fail to match
  do **not** trigger it: the peers holding the artist were already seen and a
  bare-title search would only add other artists. Off by default.
- `response_limit` / `file_limit` — slskd's per-search caps. Defaults `100` /
  `10000` are the `slskd-api` wrapper's own, which is what upstream runs
  with. Raising `response_limit` is cheap once queries name the artist;
  raising it for bare-title searches mostly adds folders to probe.

Attempts run in order until one returns responses, `minimum_search_interval`
apart: sanitized artist + title, raw artist + title, sanitized title, raw
title (sanitized forms only with `sanitize_search_queries`, duplicates
dropped). Logic and tests: `soularr_fork/queries.py`, `tests/test_queries.py`.

Also fixed here: upstream compares `search_timeout` (milliseconds) with
elapsed **seconds** while polling for a search to close, so one search slskd
never closes stalls the run for 83 minutes (seen 2026-09-27 and 09-28). The
fork polls for `search_timeout` + 60 s, then gives up on that attempt.

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

### New releases first (recent)

`[Search Settings]`

Add `recent` to `search_source` (e.g. `recent,missing,cf_below`) to search
the newest missing releases every run, ahead of the paging lap. Lidarr's
indexers rarely carry new singles/EPs, and with an artist-sorted lap of
~28k wanted albums a single released after the cursor passed its artist
waits weeks for its turn. The source reads `wanted/missing` sorted by
release date (newest first) and stops paging at the window edge; each
pick then goes through the same queue filter, type policy, F4 dedup,
staged dedup, ladders and gates as any other wanted album.

- `recent_days` — window in whole days (default `60`; `0` turns the
  source off). Lidarr leaves future releases out of the missing list.
- `recent_album_types` — Lidarr types to take (default `Single,EP`; set it
  empty to take every type — Albums still need the proof gate).
- `recent_per_run` — albums per run (default `5`; `0` turns it off).
- `recent_retry_hours` — a searched album is searched again only after
  this many hours (default `24`), so a release that is not on Soulseek yet
  is retried daily until it shows up or leaves the window. Never-searched
  albums go first (newest release first), then the longest-waiting. State:
  `recent_searched.json` in the var dir (album id -> last pick), pruned of
  albums that left the window or stopped being missing.

An album returned by two sources in one run (a new single that is also on
the current `missing` page) is searched once. List `recent` first so new
releases get the run's first searches.

Deploy order: image first, config second — an older build does not know
`recent` and would treat the unknown source as `cutoff_unmet`.

Logic and tests: `soularr_fork/recent.py`, `tests/test_recent.py`.

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
  token when unreadable). A folder with no FLAC gets the token of the audio
  it really holds instead — `[MP3-320]` / `[MP3-VBR]` from the first MPEG
  frame header (Lidarr's own `{Quality Depth}` spelling), or the upper-cased
  extension for other codecs — so an `mp3 320` rung download is never
  labelled `[FLAC]` (Lidarr's folder-parse custom formats would score it
  Lossless; nine such promotes shipped before 2026-09-24). `[LOG+CUE]`
  appears when the folder holds both a `.log` and a `.cue`; the release group
  comes from parsing the original slskd folder name, and the parser drops
  tokens that can never be a group: audio-spec fragments (`[16B-44100kHz]` →
  `44100kHz`, mirroring lidarr-fork's `InvalidReleaseGroupRegex`), hex runs
  from MusicBrainz ids in the folder name (`(9ca74894-…-70fcfe657896)`) and
  edition words (`[Single]`, `[EP]`).

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

### Startup orphan sweep

`[Download Settings]`

- `orphan_sweep` — promote fires only within the cycle that grabbed the
  album, so a container restart orphans completed downloads in the staging
  dir forever. With this on (and `promote_completed` on), every script run
  starts with one sweep over the staging dir's immediate subfolders. Off by
  default.
- `orphan_sweep_min_age` — minutes a folder must be untouched before it
  counts as settled (default `15`).
- `orphan_sweep_max_attempts` — attempts per folder before the sweep stops
  retrying it (default `4`).

Per folder the sweep: **identifies** the album (tags from the first audio
file win, Lidarr-style parsing of the leaf folder name fills the gaps; tag
artists with a featuring clause — `2Pac feat. Nate Dogg` — retry with the
clause stripped), **matches** it to a Lidarr artist/album (exact-casefold
names; on multiple title hits the album whose track count equals the
folder's audio count, else the earliest release), checks **completeness**
(folder audio count must equal the album's track count — a still-growing
folder just retries later), applies the **same proof gate** as live grabs
for proof-required types (log+cue or a matching FLAC scene name, lossless-
only audio), and **promotes** through the regular flow under the composed
token folder name.

Folders that are hidden, younger than the age floor, or still appearing in
slskd's active transfer list are skipped without counting an attempt.
Attempts and outcomes persist in `orphan_sweep.json` in the var dir; hitting
the attempt cap parks the folder for manual review instead of re-chewing it
on every restart. Everything is fail-open: one bad folder logs its outcome
and the sweep moves on.

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
album_prepend_artist = True
title_only_fallback = False
response_limit = 250
search_sort_key = artists.sortname
search_source = recent,missing,cf_below
recent_days = 60
recent_album_types = Single,EP,Album
recent_per_run = 5
recent_retry_hours = 12

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

**2026-10-31 hand merge** (the weekly sync had failed since 2026-10-12 on
conflicts in `config.ini` and `soularr.py`; until then upstream had nothing
new since the fork point `0700090`): upstream through `33116da` — Lidarr
connection errors (`LIDARR_CONNECTION_ERRORS`), enqueues slskd does not
report back count as failed, two infinite waits in download/import
monitoring, wait for slskd to move finished files, damaged
`.current_page.txt`, case/space-tolerant config lists, 10+ disc formats,
and the opt-in `search_retry_wait_hours` / `ignore_failed_users` (both off
by default). Resolution keeps the fork's structure: `execute_search` +
`poll_deadline` (upstream fixed the same 83-min stall), `iter_candidates`
with upstream's `is_skipped_user` check added, `get_records(source)`.
Two follow-ups made in the merge:
- per-type ladders are normalized like upstream's `allowed_filetypes`
  (trimmed, lowercased) so they key the same search-cache entries;
- `downloaded_file_path` takes the newest of the plain name and slskd's
  `<stem>_<ticks><ext>` collision copies — upstream's wait-for-move would
  otherwise hold the run to `stalled_timeout` (1 h) for such albums, and the
  import would move a stale namesake or fail.
Upstream deleted its tests directory (`86f5c54`); its 70 tests from
`86f5c54^` pass against the merge with only the fork's call signatures
adapted (`get_records("missing")`, `check_for_match(..., gate)`).

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
