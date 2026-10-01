#!/usr/bin/env bash
# One-time setup for the whole repo. Safe to re-run.
#   1. creates the shared Python venv (<repo>/venv): faster-whisper for
#      subtitle-generator, pytest + ruff for scripts/check (the other skills
#      only use the standard library);
#   2. checks the tools the skills call: ffmpeg/ffprobe and Ollama;
#   3. enables the pre-push hook.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d venv ]; then
    echo "Creating venv..."
    python3 -m venv venv
    venv/bin/pip install --quiet --upgrade pip
fi
venv/bin/pip install --quiet -r requirements-dev.txt faster-whisper
echo "venv ready: $(venv/bin/python -c 'import faster_whisper; print("faster-whisper", faster_whisper.__version__)')"

for tool in ffmpeg ffprobe; do
    command -v "$tool" >/dev/null || echo "MISSING: $tool (sudo apt install ffmpeg)"
done
if command -v ollama >/dev/null; then
    # captured first: grep -q closing the pipe early would fail the pipeline under pipefail
    models=$(ollama list 2>/dev/null || true)
    grep -Eq '^(qwen3|gemma3)' <<<"$models" ||
        echo "No qwen3/gemma3 model in Ollama yet: ollama pull qwen3:30b-a3b-instruct-2507-q4_K_M (or a smaller one)"
else
    echo "MISSING: ollama (subtitle-translate needs a local Ollama server)"
fi

git config core.hooksPath .githooks 2>/dev/null || true
echo "Setup complete."
