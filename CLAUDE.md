# tvshow-skills

One skill per top-level folder (`<skill>/SKILL.md` + `<skill>/scripts/`),
harness-neutral: this file is read as `CLAUDE.md` (Claude Code) and, through
a symlink, as `AGENTS.md` (Codex). The README's table lists every skill and
its scripts; read a skill's `SKILL.md` before running any of its scripts.

Skills: `subtitle-translate` (existing subtitle track → another language as
`.srt`, through a local Ollama model), `jellyfin-rename` (release names →
Jellyfin layout, dry run + undo log), `subtitle-generator` (no subtitles at
all → Whisper `.srt` from the audio). `.claude/skills/` and `.agents/skills/`
contain symlinks to those folders.

## Shared pieces at the repo root

- Root `setup.sh` creates `venv/` (faster-whisper for `subtitle-generator`,
  pytest + ruff for the gate) and enables the pre-push hook.
  `subtitle-translate` and `jellyfin-rename` are standard library only and
  run with the system `python3`.
- `tests/` covers all skills; `tests/conftest.py` puts every
  `<skill>/scripts/` on the import path and provides the fake Ollama server.

## Conventions

- **Subtitles go next to their video** (`<video name>.<lang>.srt`, no
  `.default` flag): that is where a player looks. Every other output (work
  files, plans, logs) goes to `~/Downloads/<source name> ...`
  (`SHOW_OUTPUT_DIR` replaces the root), never into the repo.
- Videos and existing subtitle files are never modified. An existing target
  `.srt` is skipped unless `--force`.
- `jellyfin-rename` is the only thing that moves the user's files: always
  `plan` → dry run → Diego sees it → `--apply`. It never deletes or
  overwrites, and `rename-log.json` undoes it.
- Jellyfin naming: `Shows/` and `Movies/` apart, TMDB numbering, the year
  only on movies, no `[tmdbid-N]` tags on folders or files.
- The translation is done by the local model, not by the agent: there is NO
  LLM API usage, and the agent does not write a show's dialogue itself. The
  agent picks flags, writes the glossary, spot-checks and reports.
- Long runs go in the background with a log; report the ETA the script
  prints. Ollama handles one request at a time, so do not start a second
  translation while one is running.
- Keep scripts inside their skill folder and document new ones in both the
  skill's `SKILL.md` and the README table.

## Testing and shipping (dev-playbook)

This repo follows the dev-playbook (`~/.claude/skills/dev-playbook/PLAYBOOK.md`).

- One gate: `scripts/check` (ruff + shellcheck + pytest). The pre-push hook runs it
  (`git config core.hooksPath .githooks`). Never push with `--no-verify`.
- Bugs: write a failing regression test first, then fix.
- Never weaken, skip or delete a test to make it pass.
- Risk class for this repo: data-deletion (the skills move and write files in a
  media library someone else owns the content of). Changes to `jellyfin-rename`
  or to where `subtitle-translate` writes need scenario tests in temp folders
  (nothing lost, nothing overwritten, dry run changes nothing, undo restores)
  and keep the second safeguard: dry run by default plus the undo log.
- External services: Ollama is always the fake server in tests; real-model runs
  are marked `slow` and run by hand. No test downloads a Whisper model.
- No real paths, IPs, passwords or tokens in code, tests, fixtures or docs.
- Before finishing: run `/code-review` on the diff, then give the evidence package:
  summary, risk class, test results, rollback steps.
