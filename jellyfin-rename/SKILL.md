---
name: jellyfin-rename
description: Rename a downloaded show (release-named files and folders) into the layout Jellyfin recognises automatically, with seasons and movies kept apart - Shows/<Series>/Season NN/<Series> - SxxEyy.ext and Movies/<Title (year)>/<Title (year)>.ext. Subtitle and other sidecar files follow their video. Dry run first, never overwrites or deletes, every move is logged and can be undone. Use when Diego asks to "rename for Jellyfin", "organize this show/season", "make Jellyfin recognize these files", "arruma os nomes pro Jellyfin", or points at a download folder of episodes and movies.
---

# jellyfin-rename — release names → Jellyfin layout

One script, `jellyfin-rename/scripts/jellyfin_rename.py` (standard library
only). `<repo>` is the tvshow-skills checkout.

```bash
python3 <repo>/jellyfin-rename/scripts/jellyfin_rename.py plan "<folder>" --series "Title" \
    [--special S01E76=S00E01:Title] [--movie "TEXT=Title|Year"]
python3 <repo>/jellyfin-rename/scripts/jellyfin_rename.py apply "<plan.json>"            # dry run
python3 <repo>/jellyfin-rename/scripts/jellyfin_rename.py apply "<plan.json>" --apply
python3 <repo>/jellyfin-rename/scripts/jellyfin_rename.py undo "<rename-log.json>" --apply
```

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

## Workflow

1. List the folder. Look the show up on TMDB (the series page and its
   seasons, including Specials): title, episodes per season. Compare with
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
5. Report: counts per kind, what was left unmatched, where the undo log is,
   and that the Shows and Movies folders go into separate Jellyfin libraries.

## Safety

- `plan` and the dry run write nothing into the folder.
- Files are moved inside the folder, never copied, deleted or overwritten;
  folders are removed only when the rename left them empty.
- `rename-log.json` (next to the plan) is written after every move; `undo`
  puts every file back under its old name, also as a dry run first.
- If a torrent client is still seeding the folder, renaming breaks the
  seeding: say so before applying.
