# tvshow-skills

Diego's agent skills for TV shows, anime and movies in a Jellyfin library:
translate the subtitles a video already has into another language with a
local model, generate subtitles from the audio when there are none, rename a
download into the layout Jellyfin recognises, and move the result into the
media library of the home server.

> These skills are tailored to this machine (Ollama with a Qwen3 model on an
> RTX 3050, Jellyfin with TMDB metadata, Diego's naming rules). Treat them as
> examples and adapt rather than reuse verbatim.

## Layout

Flat, one directory per skill, the same shape as
[comic-skills](https://github.com/diegochagas/comic-skills):

```
<skill>/SKILL.md      what the agent reads (workflow, how to pick flags from the request)
<skill>/scripts/      every script that skill runs (nothing lives outside its skill)
<skill>/examples/     skill-owned assets
tests/                pytest suite for all skills (fake Ollama server, temp folders)
scripts/check         the one gate: ruff + shellcheck + pytest
```

The only shared, machine-generated piece is `venv/`, created by
**`./setup.sh`**: faster-whisper for `subtitle-generator`, pytest and ruff
for `scripts/check`. The other skills use only the Python standard library,
plus the tools they drive: `ffmpeg`/`ffprobe` and a running Ollama
(`subtitle-translate`), `rsync` and `ssh` (`library-move`).

## Skills

| Skill | Scripts | What it does |
| --- | --- | --- |
| [`subtitle-translate`](subtitle-translate/) | `translate_subs.py`, `sublib.py`, `examples/` | Extracts the subtitle track of a video or of every video under a folder (embedded ASS/SRT/mov_text/WebVTT, or a sidecar file) and translates it with a local Ollama model, 20 lines at a time with the previous lines as context, on the original timings. Validates every answer (all lines present, italics and line breaks kept), retries what comes back broken, never drops a line. Writes `<video name>.<lang>.srt` next to each video as soon as it is done; resumable; `--recheck` re-translates lines that look wrong; `--extract-only` just extracts. |
| [`jellyfin-rename`](jellyfin-rename/) | `jellyfin_rename.py` | Plans and applies the rename of a download into `Shows/<Series>/Season NN/<Series> - SxxEyy.ext` and `Movies/<Title (year)>/…`, with specials and movies mapped by the agent from TMDB. Sidecar files follow their video. Dry run by default, refuses to overwrite, logs every move, `undo` restores the old names. |
| [`library-move`](library-move/) | `library_move.py`, `config.example.json` | Moves a prepared folder (`Shows/`, `Movies/`) into the server's media library: rsync over SSH into the folders named in a gitignored `config.json`, every file verified on the server (checksum by default), then the local copies go to the trash. Dry run by default, never overwrites a file already in the library, can start a Jellyfin scan at the end. |
| [`subtitle-generator`](subtitle-generator/) | `gen_subs.py`, `run_season.sh` | For videos with no subtitles: faster-whisper turns the audio into a synchronized `.srt`, translated to English or in the original language, one episode at a time, resumable in windows. |

## Rules

| Skill | Writes | Never |
| --- | --- | --- |
| `subtitle-translate` | `<video name>.<lang>.srt` next to the video; work files in `~/Downloads/<source name> subtitles/` | modifies a video or an existing subtitle; overwrites a `.srt` that is already there (without `--force`) |
| `jellyfin-rename` | `plan.json` and `rename-log.json` in `~/Downloads/<folder name> rename/`; moves files inside the folder only with `--apply` | deletes, copies or overwrites a file; moves anything out of the folder |
| `subtitle-generator` | `<video name>.en.srt` next to the video (+ a progress file while running) | touches the video |
| `library-move` | the series and movie folders into the library, only with `--apply`; local copies to the trash after verification | overwrites or deletes a library file; removes a local copy that was not verified; deletes (it trashes) |

`SHOW_OUTPUT_DIR` replaces `~/Downloads` as the root for work files.

Naming rules for Jellyfin: seasons and movies in separate folders, TMDB
numbering, **the year only on movies**, no `[tmdbid-N]` tags, subtitles as
`.srt` named `<video name>.<lang>.srt`.

## Local models

`subtitle-translate` asks Ollama for the strongest model installed, in this
order: `qwen3:30b-a3b-instruct`, `qwen3:30b`, `qwen3:14b`, `gemma3:27b`,
`gemma3:12b`, `qwen3:8b`, `gemma3:4b` (`--model` or `$SUBTITLE_MODEL`
override it; `$OLLAMA_HOST` points at another server). On French → English
the 30b instruct model kept line breaks and dialogue dashes and read
naturally at about 60–75 lines a minute; `gemma3:4b` was faster but dropped
line breaks, `qwen3:8b` shouted in capitals.

## Setup and checks

```sh
./setup.sh        # venv, tool checks, enables the pre-push hook
scripts/check     # ruff + shellcheck + pytest (what the hook and CI run)
```

The tests never call a real model: Ollama is a fake HTTP server, and the
rename tests work on temp folders.

## Consumers

`.claude/skills/` and `.agents/skills/` hold symlinks into the skill folders,
so the skills load when an agent starts inside this repo. To use them from
anywhere:

```sh
for s in subtitle-translate jellyfin-rename subtitle-generator library-move; do
  for h in ~/.claude/skills ~/.agents/skills ~/.codex/skills; do
    mkdir -p "$h" && ln -sfn ~/Projects/tvshow-skills/$s "$h/$s"
  done
done
```
