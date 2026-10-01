"""Shared fixtures: skill scripts on the import path and a fake Ollama server."""

import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
for skill in (
    "subtitle-translate",
    "jellyfin-organizer",
    "subtitle-generator",
    "library-move",
    "subtitle-sync",
):
    sys.path.insert(0, str(REPO / skill / "scripts"))

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 640
PlayResY: 360

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, \
Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, \
Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,22,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,1,2,2,2,25,1
Style: Thought,Arial,22,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,-1,0,0,100,100,0,0,1,1,1,2,2,2,25,1
Style: Top,Arial,22,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,1,8,2,2,25,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def make_ass(texts, style="Default"):
    """An ASS document with one two-second event per text."""
    rows = []
    for n, text in enumerate(texts):
        if isinstance(text, tuple):
            text, row_style = text
        else:
            row_style = style
        start, end = n * 3, n * 3 + 2
        rows.append(f"Dialogue: 0,0:00:{start:02d}.00,0:00:{end:02d}.50,{row_style},,0,0,0,,{text}")
    return ASS_HEADER + "\n".join(rows) + "\n"


class FakeOllama:
    """Answers /api/chat by 'translating' each requested line with `translate(id, text)`."""

    def __init__(self):
        self.requests = []
        self.models = ["qwen3:8b"]
        self.reject_think = False
        self.translate = lambda i, text: "EN " + text

    def lines_asked(self):
        return [i for req in self.requests for i in req["ids"]]


@pytest.fixture
def ollama(monkeypatch):
    fake = FakeOllama()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send(200, {"models": [{"name": m} for m in fake.models]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if fake.reject_think and "think" in body:
                self._send(400, {"error": "this model does not support thinking"})
                return
            user = body["messages"][-1]["content"]
            asked = user.split("Translate these lines:\n", 1)[1]
            rows = [re.match(r"(\d+)\|(.*)$", row) for row in asked.splitlines()]
            rows = [(int(m.group(1)), m.group(2)) for m in rows if m]
            fake.requests.append({"body": body, "ids": [i for i, _ in rows], "user": user})
            answer = []
            for i, text in rows:
                out = fake.translate(i, text)
                if out is not None:
                    answer.append(f"{i}|{out}")
            self._send(200, {"message": {"content": "\n".join(answer)}, "eval_count": 10})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("OLLAMA_HOST", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.delenv("SUBTITLE_MODEL", raising=False)
    yield fake
    server.shutdown()
    server.server_close()


def tree(root):
    """Every file under a folder with its content, for before/after comparisons."""
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }
