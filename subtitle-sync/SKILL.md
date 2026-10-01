---
name: subtitle-sync
description: Check whether the .srt subtitles next to a video (or every video under a folder) are in sync with its audio, and fix their timing when they are not - a constant offset (subtitles early or late) or a frame-rate drift (25 vs 23.976 fps). Compares the speech in the audio with when subtitles are on screen; the originals are backed up before anything changes, dry run by default, undo restores them. Use when Diego asks to "sync the subtitles", "the subtitles are late/early/out of sync", "check the sync", "ajusta o tempo das legendas", "legenda dessincronizada", or after subtitle-translate / jellyfin-organizer when subtitles came from another release. Not for creating subtitles (subtitle-generator) or translating them (subtitle-translate).
---

# subtitle-sync — are the subtitles on the audio, and move them if not

One script, `subtitle-sync/scripts/sync_subs.py`. Needs `ffmpeg` and numpy,
so run it with the repo venv (`<repo>/venv/bin/python`, from `setup.sh`).
`<repo>` is the tvshow-skills checkout. No model and no network: it is signal
processing, about 3 s per episode.

```bash
<repo>/venv/bin/python <repo>/subtitle-sync/scripts/sync_subs.py "<video or folder>"            # check
<repo>/venv/bin/python <repo>/subtitle-sync/scripts/sync_subs.py "<video or folder>" --apply    # fix
<repo>/venv/bin/python <repo>/subtitle-sync/scripts/sync_subs.py --undo "<sync-log.json>" --apply
```

**Progress:** the script prints `FINISHED <episode file name>` as soon as each episode is done; relay those names to Diego as they appear in the log, in the final report too.

## How it decides

1. The first audio track becomes a speech curve (voice-band energy every
   10 ms); each `.srt` next to the video (`<video name>.*.srt`) becomes a
   "subtitle on screen" curve.
2. The curves are correlated: the best match gives the offset. The common
   frame-rate ratios (25/23.976, 24/23.976, 30/29.97, ...) are tried too.
3. 5-minute windows then check the offset along the video: a straight line
   means drift (fixed as a scale), anything else is reported.

| Verdict | Meaning | `--apply` |
| --- | --- | --- |
| `ok` | within `--tolerance` (0.3 s) of the audio | nothing |
| `offset` | the whole file is early or late | shifts every cue |
| `drift` | grows along the video (frame rate) | scales and shifts |
| `variable` | the offset jumps (cuts, ads, other edit) | left alone, reported |
| `low-confidence` | no clear match (music-only, wrong episode, other language track) | left alone |
| `broken` | too few cues, or the file ends in the first 40 % of the video | left alone |
| `no-audio` | the video has no readable audio | left alone |

A fix never changes the text, only the `-->` times, and never goes below 0.

## Flags

| Flag | Use |
| --- | --- |
| `--apply` | write the fixes (default: report only) |
| `--fix offset` | with `--apply`, fix only these verdicts (`offset`, `drift`; default both). Small drifts are the least reliable: when unsure, fix offsets only |
| `--undo LOG` | restore the originals from a `sync-log.json` (dry run without `--apply`) |
| `--tolerance S` | offset or drift below this is "ok" (default 0.3) |
| `--max-shift S` | largest offset to look for (default 120) |
| `--audio-track N` | use another audio stream (0 = first); pick the one the subtitles were made for |
| `--only STR...` / `--limit N` | restrict the videos |
| `--output DIR` | work folder (default `~/Downloads/<source name> sync/`) |

## Workflow

1. Check without `--apply` and read the list. Show Diego the counts per
   verdict and the biggest shifts.
2. Spot-check one fix when it is large (more than ~10 s) or a drift: play the
   moment of a line in the video, or compare the first and last cue with the
   audio. A subtitle for another cut of the show can look like an offset.
3. `--apply` after Diego agrees (or when he already asked to fix them).
4. Report: counts, what was left alone and why, and where the work folder
   (`report.json`, `backup/`, `sync-log.json`) is. Several subtitle files of
   one video are each checked on their own.

## Safety

- The only file written into the source folder is the `.srt` being fixed, and
  only with `--apply`; videos are never touched.
- Before each fix the original goes to `<work>/backup/<same relative path>`;
  it never overwrites an existing backup.
- A backup is never overwritten (a second one gets `.2`), and a later run into
  the same work folder adds to `sync-log.json` instead of replacing it.
- `sync-log.json` is written after every fix. `--undo` refuses a file that was
  edited after the fix, so nothing newer is lost.

## Limits

- It measures where speech is, not what is said: a subtitle of the wrong
  episode or with strong music under it can match badly. `low-confidence`
  means "do not trust", not "in sync".
- A subtitle with its timing broken inside (text out of order) cannot be
  fixed by moving it; use `subtitle-generator` or another file.
- SRT only; ASS/SSA files are not read.
