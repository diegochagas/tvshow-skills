import shutil
import subprocess
import sys
import types
from typing import ClassVar

import gen_subs
import pytest


def test_fmt_ts():
    assert gen_subs.fmt_ts(0) == "00:00:00,000"
    assert gen_subs.fmt_ts(3723.4567) == "01:02:03,457"
    assert gen_subs.fmt_ts(59.9996) == "00:01:00,000"
    assert gen_subs.fmt_ts(-1) == "00:00:00,000"


def test_write_srt_sorts_and_skips_empty(tmp_path):
    out = tmp_path / "a.srt"
    count = gen_subs.write_srt(
        [
            {"start": 5.0, "end": 6.0, "text": " second "},
            {"start": 1.0, "end": 2.0, "text": "first"},
            {"start": 3.0, "end": 4.0, "text": "  "},
        ],
        out,
    )
    assert count == 2
    assert out.read_text() == (
        "1\n00:00:01,000 --> 00:00:02,000\nfirst\n\n2\n00:00:05,000 --> 00:00:06,000\nsecond\n\n"
    )


class FakeSegment:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


class FakeWhisper:
    """Stands in for faster_whisper: Japanese speech, one line per window."""

    calls: ClassVar[list] = []

    def __init__(self, *args, **kwargs):
        pass

    def transcribe(self, wav, **kwargs):
        FakeWhisper.calls.append(kwargs)
        info = type("Info", (), {"language": "ja"})()
        return iter([FakeSegment(0.5, 1.5, "こんにちは")]), info


@pytest.fixture
def fake_whisper(monkeypatch):
    FakeWhisper.calls = []
    module = types.ModuleType("faster_whisper")
    module.WhisperModel = FakeWhisper
    monkeypatch.setitem(sys.modules, "faster_whisper", module)


@pytest.fixture
def clip(tmp_path):
    video = tmp_path / "Show - S01E01.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "3"]
        + ["-c:a", "flac", str(video)],
        check=True,
    )
    return video


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


@needs_ffmpeg
def test_default_transcribes_then_translates_with_subtitle_translate(
    fake_whisper, clip, monkeypatch
):
    asked = []
    monkeypatch.setattr(
        gen_subs, "translate_subtitles", lambda v, to: asked.append((v, to)) or True
    )
    monkeypatch.setattr(sys, "argv", ["gen_subs.py", str(clip)])
    gen_subs.main()
    assert (clip.parent / "Show - S01E01.ja.srt").read_text().count("こんにちは") == 1
    assert FakeWhisper.calls[0]["task"] == "transcribe"
    assert asked == [(str(clip), "en")]


@needs_ffmpeg
def test_to_none_and_whisper_translate_do_not_call_the_translator(fake_whisper, clip, monkeypatch):
    asked = []
    monkeypatch.setattr(gen_subs, "translate_subtitles", lambda v, to: asked.append(to) or True)
    monkeypatch.setattr(sys, "argv", ["gen_subs.py", str(clip), "--to", "none"])
    gen_subs.main()
    assert (clip.parent / "Show - S01E01.ja.srt").exists() and asked == []
    monkeypatch.setattr(sys, "argv", ["gen_subs.py", str(clip), "--task", "translate"])
    gen_subs.main()
    assert (clip.parent / "Show - S01E01.en.srt").exists() and asked == []


@needs_ffmpeg
def test_failed_translation_keeps_the_transcript_and_exits_with_error(
    fake_whisper, clip, monkeypatch, capsys
):
    monkeypatch.setattr(gen_subs, "translate_subtitles", lambda v, to: False)
    monkeypatch.setattr(sys, "argv", ["gen_subs.py", str(clip)])
    with pytest.raises(SystemExit) as exit_info:
        gen_subs.main()
    assert exit_info.value.code == 1
    out = capsys.readouterr().out
    assert "TRANSLATION FAILED" in out and "FINISHED" not in out
    assert (clip.parent / "Show - S01E01.ja.srt").exists()


@needs_ffmpeg
def test_an_existing_transcript_is_never_overwritten_or_deleted(fake_whisper, clip, monkeypatch):
    mine = clip.parent / "Show - S01E01.ja.srt"
    mine.write_text("1\n00:00:00,000 --> 00:00:01,000\nmy own file\n\n", encoding="utf-8")
    asked = []
    monkeypatch.setattr(gen_subs, "translate_subtitles", lambda v, to: asked.append(to) or True)
    monkeypatch.setattr(sys, "argv", ["gen_subs.py", str(clip)])
    gen_subs.main()
    assert "my own file" in mine.read_text() and "こんにちは" not in mine.read_text()
    assert asked == ["en"]  # it is translated as it is


@needs_ffmpeg
def test_no_speech_does_not_delete_an_existing_transcript(fake_whisper, clip, monkeypatch):
    mine = clip.parent / "Show - S01E01.ja.srt"
    mine.write_text("1\n00:00:00,000 --> 00:00:01,000\nmy own file\n\n", encoding="utf-8")
    monkeypatch.setattr(FakeWhisper, "transcribe", lambda self, wav, **kw: (iter([]), FakeInfo()))
    monkeypatch.setattr(gen_subs, "translate_subtitles", lambda v, to: True)
    monkeypatch.setattr(sys, "argv", ["gen_subs.py", str(clip), "--to", "none"])
    gen_subs.main()
    assert "my own file" in mine.read_text()


@needs_ffmpeg
def test_rerun_after_a_failed_translation_reuses_the_transcript(fake_whisper, clip, monkeypatch):
    results = iter([False, True])
    monkeypatch.setattr(gen_subs, "translate_subtitles", lambda v, to: next(results))
    monkeypatch.setattr(sys, "argv", ["gen_subs.py", str(clip)])
    with pytest.raises(SystemExit):
        gen_subs.main()
    first_calls = len(FakeWhisper.calls)
    gen_subs.main()
    assert len(FakeWhisper.calls) == first_calls  # Whisper did not run again
    assert not (clip.parent / "Show - S01E01.subprogress.json").exists()


class FakeInfo:
    language = "ja"
