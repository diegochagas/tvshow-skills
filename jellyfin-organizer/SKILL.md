---
name: jellyfin-organizer
description: Organize a downloaded show (release-named files and folders, several season folders, extras, loose subtitles) into the layout Jellyfin recognises automatically, with seasons and movies kept apart - Shows/<Series>/Season NN/<Series> - SxxEyy.ext and Movies/<Title (year)>/<Title (year)>.ext. Subtitle and other sidecar files follow their video. Dry run first, never overwrites or deletes, every move is logged and can be undone. Titles, numbering and specials always come from https://www.themoviedb.org first. Use when Diego asks to "rename for Jellyfin", "organize this show/season", "make Jellyfin recognize these files", "arruma os nomes pro Jellyfin", or points at a download folder of episodes and movies.
---

# jellyfin-organizer — downloads → Jellyfin layout

One script, `jellyfin-organizer/scripts/jellyfin_organizer.py` (standard library
only). `<repo>` is the tvshow-skills checkout.

```bash
python3 <repo>/jellyfin-organizer/scripts/jellyfin_organizer.py plan "<folder>" --series "Title" \
    [--special S01E76=S00E01:Title] [--movie "TEXT=Title|Year"]
python3 <repo>/jellyfin-organizer/scripts/jellyfin_organizer.py apply "<plan.json>"            # dry run
python3 <repo>/jellyfin-organizer/scripts/jellyfin_organizer.py apply "<plan.json>" --apply
python3 <repo>/jellyfin-organizer/scripts/jellyfin_organizer.py undo "<rename-log.json>" --apply
```

**Progress:** the script prints `FINISHED <episode file name>` as soon as each episode is done; relay those names to Diego as they appear in the log, in the final report too.

## Naming rules (Diego's)

```
<folder>/
├── Shows/<Series>/
│   ├── Season 00/<Series> - S00E01 - <Title>.mkv     specials
│   └── Season 01/<Series> - S01E01.mkv
│                 <Series> - S01E01.en.srt            sidecars keep their suffix
└── Movies/<Title> (<year>)/<Title> (<year>).mkv
```

- **Seasons and movies are separated** (`Shows/` and `Movies/`): Jellyfin
  uses one library per kind.
- **Only movies carry the year.** Series folders and episode files do not.
- **No `[tmdbid-N]` tags** on folders or files. Use the exact title TMDB has
  for the series and for each movie, so Jellyfin matches by name; if it
  picks the wrong entry, fix it once with Identify in Jellyfin.
- Numbering follows TMDB (Jellyfin's default provider), not the release.

## `plan` flags

| Flag | Use |
| --- | --- |
| `--series "Title"` | series name used for the folder and every episode file (required) |
| `--special S01E76=S00E01:Title` | an episode the release numbers differently from TMDB (repeatable); the title is optional |
| `--movie "TEXT=Title\|Year"` | a file whose name contains TEXT is a movie (repeatable) |
| `--title S01E01=Episode title` | add an episode title to a file name (repeatable; rarely needed) |
| `--season N` | files numbered without `SxxEyy` (`Show - 07 [1080p]`) belong to season N |
| `--output DIR` | where `plan.json` goes (default `~/Downloads/<folder name> rename/`) |

Recognised episode patterns: `S01E02`, `s01.e02`, `1x02`, `S01E01-E02`, and
with `--season` a bare episode number.

## Source of truth: TMDB first

Before any plan, look the show or movie up on https://www.themoviedb.org
(search, then `/tv/<id>/seasons` and `/tv/<id>/season/0`; `WebFetch` works).
Take the exact title, the episodes per season and the Specials list from
there, and only then compare with the files. Other sites (TheTVDB, MAL) are
a fallback when TMDB has no entry. Match extras to TMDB specials by name and
duration (`ffprobe`); leave anything uncertain where it is.

## Workflow

1. List the folder and check it against TMDB (above). Compare with
   the files: an extra episode at the end of a season is usually a special;
   OVAs and films are usually separate TMDB movies (title and year).
2. `plan` with the mappings. Read its summary: every `UNMATCHED` file stays
   where it is, so either map it (`--movie`, `--special`, `--season`) or
   tell Diego it was left alone.
3. `apply` without `--apply` and show Diego the list (or a few lines per
   season when it is long). **Rename only after Diego agrees**, unless the
   request already was "rename them".
4. `apply --apply`. It refuses to run if any target exists or two files
   would get the same name, and changes nothing in that case.
5. Subtitles: every loose `.srt` whose season and episode number match an
   episode is moved next to that video as `<name>.<lang>.srt` (language from
   the file name or its text; `.en.2.srt` when that name is taken). A
   subtitle is never left out because it looks bad: check its last timestamp
   against the video length and its text, and only report the broken or
   truncated ones. Use a second plan (own `--output` folder, own undo log).
   Only files with no matching episode go to `Extras/Unmatched subs/`.
6. Report: counts per kind, what was left unmatched, where the undo log is,
   and that the Shows and Movies folders go into separate Jellyfin libraries.

## Messy downloads

- **Several season folders** with bare numbers (`S2 - 01`): `--season N`
  applies to the whole folder, so plan each season folder separately and
  merge the moves into one plan rooted at the top folder.
- **`EXTRA/` folders** (NCOP/NCED, CMs, interviews, scans): with `--season`
  they would be numbered as episodes (`NCOP - 01` becomes `S01E01`). Drop
  those moves from the plan and map only the TMDB specials you are sure of
  to `Shows/<Series>/Season 00/<Series> - S00Exx - <Title>.mkv`.

## Folder names

The download folder is organized too, not only the files:
- The top folder drops the release tag and takes the TMDB title
  (`[Moozzi2] Bakuman` becomes `Bakuman`). Do this last, with `mv`, and then
  point `root` in every `plan.json` / `rename-log.json` at the new path so
  `undo` still works.
- Release-named leftover folders are not kept: their files move (plan, undo
  log) to `Extras/Season NN/…`, and subtitles that match no episode to
  `Extras/Unmatched subs/`. The emptied release folders disappear.

## Safety

- `plan` and the dry run write nothing into the folder.
- Files are moved inside the folder, never copied, deleted or overwritten;
  folders are removed only when the rename left them empty.
- `rename-log.json` (next to the plan) is written after every move; `undo`
  puts every file back under its old name, also as a dry run first.
- If a torrent client is still seeding the folder, renaming breaks the
  seeding: say so before applying.
