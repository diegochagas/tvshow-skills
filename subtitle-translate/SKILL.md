---
name: subtitle-translate
description: Extract the subtitle track of a video, or of every video under a folder (a season, a whole show, movies), and translate it into another language with a local Ollama model, free and offline, keeping the original timings. Writes one .srt per video next to it, named <video name>.<lang>.srt, so Jellyfin and other players pick it up. Reads embedded text tracks (ASS/SSA, SRT, mov_text, WebVTT) or a sidecar subtitle file; resumes where it stopped. Use when Diego asks to "translate the subtitles", "extract the subtitles", "legenda em inglês/português para esses episódios", "I only have French/Spanish subs", or wants subtitles in another language for a show that already has a subtitle track. Not for videos with no subtitles at all (that is subtitle-generator).
---

# subtitle-translate — existing subtitles → another language, as .srt

One script, `subtitle-translate/scripts/translate_subs.py` (standard library
only; needs `ffmpeg`/`ffprobe` and a running Ollama). `<repo>` is the
tvshow-skills checkout. The translation is done by a **local model in Ollama**:
no API, no credits, nothing leaves the computer. The agent runs the script
and checks the result; it does not write the translation itself.

```bash
python3 <repo>/subtitle-translate/scripts/translate_subs.py "<video or folder>" --to en \
    --show "a boxing anime" --glossary <file>
```

**Progress:** the script prints `FINISHED <episode file name>` as soon as each episode is done; relay those names to Diego as they appear in the log, in the final report too.

## Arguments

`/subtitle-translate <video or folder> [language]`

- `video or folder`: one video, or a folder walked recursively (every
  `.mkv/.mp4/.m4v/.avi/.webm/.mov/.ts`). If missing, ask.
- `language` → `--to` (default `en`; `pt-BR`, `es`, ... also name the file:
  `<video name>.<lang>.srt`).

| Flag | Use |
| --- | --- |
| `--to LANG` | target language code (default `en`) |
| `--from LANG` | pick the track in this language when a video has several |
| `--track N` | pick the track by its ffprobe stream index |
| `--show "TEXT"` | what is being watched ("a boxing anime", "a French police drama"); goes into the prompt |
| `--glossary FILE` | names and vocabulary for the model (see `examples/`); write one for a long show |
| `--model MODEL` | another Ollama model (`$SUBTITLE_MODEL`; default: the strongest installed of qwen3 30b → gemma3 4b) |
| `--only STR...` | only videos whose path contains one of the strings |
| `--limit N` | translate at most N videos, then stop: for a test run |
| `--extract-only` | only extract the tracks (`.ass` + `.srt`) into the work folder |
| `--recheck` | re-translate the lines that look wrong (unchanged, or far too long/short) |
| `--status` | list what is done, partial and pending; changes nothing |
| `--force` | redo videos that already have the `.srt` |
| `--name NAME` / `--output DIR` | name or place of the work folder |

## Where results go

- The translated subtitle: **next to the video**, `<video name>.<lang>.srt`
  (no `.default` flag). This is the one thing written into the source folder,
  because that is where a player looks for it. Nothing there is modified or
  overwritten: a video that already has that file is skipped.
- Everything else: `~/Downloads/<source name> subtitles/` (`SHOW_OUTPUT_DIR`
  replaces `~/Downloads`): `src/` (extracted originals, plus a small JSON
  saying which track each came from), `tr/<lang>/` (translations so far, one
  JSON per video), `<lang>/` (a copy of every `.srt`). The same work folder
  serves several target languages; asking for another track (`--track`,
  `--from`) re-extracts and drops that video's old translations.

## Workflow

1. Look at what the videos have: `ffprobe` one of them. Text tracks (ASS,
   SRT) work; image tracks (PGS, VobSub) are skipped with a message, and a
   video with no subtitles at all needs `subtitle-generator`.
2. For a long show, write a short glossary (character names as the source
   spells them, recurring terms and how to translate them) and a `--show`
   description. `examples/hajime-no-ippo.txt` shows the shape.
3. **Test run:** `--limit 1`. Read 15–20 lines of the `.srt` next to the
   source in `src/` and check: every line present, breaks and italics kept,
   names untouched, terms follow the glossary. Fix the glossary and rerun
   with `--force --limit 1` if not.
4. **Full run** in the background, output to a log. It prints one line per
   video with lines/min and an ETA (about 60–75 lines/min with
   `qwen3:30b-a3b-instruct` on the RTX 3050: a 260-line episode takes ~4 min,
   a 129-file show ~10 h). Each `.srt` appears as soon as its video is done.
   Stopping is safe: the same command continues where it stopped.
5. When it ends, run `--recheck` once, then `--status`.
6. Report: videos translated / skipped (and why), lines left untranslated,
   what `--recheck` fixed, where the work folder is. Say plainly that it is
   a machine translation.

## Limits

- A translation of a translation keeps the source subtitle's mistakes.
- The model sees 20 lines at a time plus the 6 before them: pronouns and
  recurring terms can drift in long scenes. The glossary is the lever.
- SRT keeps italics and top-of-screen placement; fonts, colours and sign
  positions of an ASS track are dropped.
- A line the model cannot translate after three tries keeps its source text
  in the `.srt` (never dropped) and is counted as "untranslated" in the log;
  `--recheck` tries it again.
- A video where more than a tenth of the lines fail (and more than 3) is
  **not installed** (`NOT INSTALLED` in the log), and the run **stops**
  (`STOPPED`, exit code 1) when Ollama does not answer. Neither leaves a
  half-translated `.srt` behind: fix the model/server and run the same
  command again.
