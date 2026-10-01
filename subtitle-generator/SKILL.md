---
name: subtitle-generator
description: Generate synchronized subtitles for a single video file (one episode at a time) from its audio, using Whisper. Use when the user wants to create, generate, or add subtitles/captions/.srt files for a video that has no subtitles — especially foreign-language audio (e.g. Japanese anime) that should be translated to English. Triggers include "generate subtitles", "make subtitles", "subtitle this episode", "add captions", "transcribe this video", ".srt". For a video that already has a subtitle track in another language, use subtitle-translate instead (it keeps the original timings and reads better).
---

# subtitle-generator — audio → .srt, one episode at a time

Creates a synchronized `.srt` next to a single video using
[faster-whisper](https://github.com/SYSTRAN/faster-whisper). It can translate
any-language audio to English (Whisper's `translate` task) or transcribe in
the original language. It is **resumable in chunks**: each run processes
time-windows and saves progress, and re-running continues until the full
`.srt` is written.

Scripts in `subtitle-generator/scripts/`, run with the repo venv
(`<repo>/venv/bin/python`, created by `<repo>/setup.sh`, which installs
faster-whisper). `<repo>` is the tvshow-skills checkout. Needs `ffmpeg` on PATH.
The model (~480 MB for `small`) downloads on first use.

## When to use

- A video (or a folder of videos) with **no subtitles**.
- English subtitles from foreign-language audio, or a plain transcript.
- One file per invocation. For a whole season use `run_season.sh`, which
  calls it once per episode and skips files that already have a `.en.srt`.

## Usage

```bash
<repo>/venv/bin/python subtitle-generator/scripts/gen_subs.py "/path/to/Episode 01.mp4"
# -> writes "/path/to/Episode 01.en.srt"

PATH="<repo>/venv/bin:$PATH" bash subtitle-generator/scripts/run_season.sh "/path/to/Season 01" [model]
```

Inside a runtime-capped sandbox, process a few windows per call and repeat
the SAME command until it prints `DONE`:

```bash
<repo>/venv/bin/python subtitle-generator/scripts/gen_subs.py "episode.mp4" --max-chunks 1 --chunk-sec 120
```

| Flag | Use |
| --- | --- |
| `--model` | `tiny\|base\|small\|medium\|large-v3` (default `small`). Bigger = better, much slower on CPU |
| `--task` | `translate` (English output, default) or `transcribe` (keep the source language) |
| `--language` | source language code, e.g. `ja` (default: auto-detect) |
| `--beam` | beam size (default 5; 1 is ~2x faster, slightly worse) |
| `--chunk-sec` | window length for resumable processing (default 300) |
| `--max-chunks` | windows to process this run (0 = run to completion) |
| `--threads` | CPU threads (default: all cores) |
| `--output` | explicit `.srt` path (default: next to the video) |

## Where results go

Next to the video: `<video name>.en.srt` (`translate`) or
`<video name>.<language>.srt` (`transcribe`), the names a player looks for.
While a file is in progress a `<video name>.subprogress.json` sits beside
it; it is deleted when the `.srt` is written. Nothing else is touched.

## How it works

1. `ffprobe` reads the duration; `ffmpeg` extracts 16 kHz mono audio per window.
2. faster-whisper transcribes/translates each window with VAD filtering
   (skips silence and music such as opening/ending themes, which reduces
   hallucinated lines) and `condition_on_previous_text=False` (avoids
   repetition loops).
3. Segments accumulate in the progress sidecar with absolute timestamps;
   when the whole file is processed the final `.srt` is written.

## Notes and limits

- Machine-generated: usable and synchronized, but imperfect. Proper nouns
  and character names are often approximated. For higher quality use
  `--model medium` (or `large-v3`), or hand-edit the `.srt`.
- CPU speed on dense dialogue is roughly 3–5x realtime with `small`/beam 5:
  a ~22-minute episode is ~5–7 minutes of compute.
- Whisper only translates **to English**. For another language, generate
  the English `.srt` and run `subtitle-translate` on it (`--to pt-BR`).

## Report

Which files got a `.srt`, how many lines each, which were skipped because
they already had one, and the model used.
