---
name: transcribe-translate
description: Turn an audio or video file (a talk, podcast, interview, lecture, episode) into a readable text transcript translated to Brazilian Portuguese (or another language), with two local models and no API - faster-whisper for the speech, the local Ollama model for the translation. Writes <name>.<source lang>.txt and <name>.pt-BR.txt in ~/Downloads/<name> transcript/. Use when Diego asks to "transcribe this", "transcreve esse áudio/vídeo", "what is said in this video, in Portuguese", "put this podcast in text", "traduz esse áudio pra português", or gives a media file and wants its content as text. Not for subtitles (.srt): that is subtitle-generator (no subtitle track) or subtitle-translate (existing track).
---

# transcribe-translate — audio/video → translated text

One script, `scripts/transcribe_translate.py`, run with the repo venv
(`<repo>/venv/bin/python`, created by `<repo>/setup.sh`; `<repo>` is the
tvshow-skills checkout). Needs `ffmpeg`/`ffprobe` on PATH and a running
Ollama for the translation. Quality first: the Whisper default is `medium`
(~1.5 GB, downloaded on first use) and the translation goes paragraph by
paragraph with the previous paragraph as context, through the strongest
Ollama model installed (same choice as subtitle-translate).

```bash
<repo>/venv/bin/python <repo>/transcribe-translate/scripts/transcribe_translate.py "/path/to/talk.mp4"
# -> ~/Downloads/talk transcript/talk.en.txt      (what was said)
#    ~/Downloads/talk transcript/talk.pt-BR.txt   (the translation)
```

| Flag | Use |
| --- | --- |
| `--to LANG` | target language (default `pt-BR`) |
| `--language CODE` | source language when auto-detection could be wrong (mixed languages, short files) |
| `--model` | Whisper `small` (fast, rough), `medium` (default), `large-v3` (best, ~3 GB, slow on CPU) |
| `--device cuda --compute float16` | use the GPU when CUDA libraries are available (default: CPU int8) |
| `--about "..."` | what the recording is: helps the translator with register and vocabulary |
| `--glossary FILE` | names and terms the translation must keep |
| `--timestamps` | prefix each paragraph with `[hh:mm:ss]` |
| `--transcript-only` | stop after the Whisper text |
| `--ollama-model NAME` | override the Ollama model (`$SUBTITLE_MODEL` also works) |
| `--output PATH` | write the translated `.txt` somewhere else |

## Workflow

1. Confirm the file exists and is audio or video. Ask only when the target
   language is not pt-BR and not stated.
2. Pass `--about` whenever Diego said what the recording is, and `--language`
   when he said which language is spoken.
3. Run in the background with a log; a 1-hour talk is roughly 20–40 minutes
   of Whisper `medium` on the CPU plus a few minutes of translation. Relay
   the `%` lines and `translated n/m paragraphs` from the log.
4. When it prints `FINISHED <file>`, open the `.pt-BR.txt`, read two or
   three paragraphs from different parts, and report: where the files are,
   how many paragraphs, the detected language, and anything that looks off
   (a `WARNING: n paragraphs kept in <lang>` line means the model refused
   those; re-running the same command retries only them).

## Where results go

`~/Downloads/<file name> transcript/` (`SHOW_OUTPUT_DIR` replaces
`~/Downloads`): `<name>.<lang>.txt`, `<name>.pt-BR.txt`, plus the work files
`segments.json` (Whisper output, resumable per 10-minute window) and
`tr.pt-BR.json` (translated paragraphs, resumable per batch). The media file
is never touched. Re-running the same command continues; a different
`--model` starts the transcription over.

## How it works

- `ffmpeg` extracts 16 kHz mono audio in 10-minute windows; faster-whisper
  transcribes each with VAD (silence and music skipped) and no conditioning
  on previous text (no repetition loops).
- Segments become paragraphs: a pause of 1.6 s or more starts a new one, and
  a paragraph is closed at the next sentence end once it passes ~600
  characters.
- Paragraphs go to Ollama in batches of ~1800 characters as `id|text` rows
  (the subtitle-translate prompt machinery), with the previous translated
  paragraph as context. An answer is accepted only if every paragraph comes
  back non-empty, different from the source and not cut short; what fails
  is asked again, then alone, then kept in the source language and listed.
- If the detected language already is the target, only the transcript is
  written.

## Limits

- Machine transcription: names and technical terms are approximated;
  `--glossary` and `--about` help, `--model large-v3` helps more.
- Several speakers are not separated (no diarization).
- Ollama answers one request at a time: do not run it alongside a
  subtitle-translate job.
