import json
import sys
import types
from typing import ClassVar

import pytest
import transcribe_translate as tt


def seg(start, end, text):
    return {"start": start, "end": end, "text": text}


def test_paragraphs_split_on_pause_and_size():
    segs = [
        seg(0, 1, "Hello there."),
        seg(1.2, 2, "How are you?"),
        seg(4.0, 5, "A new topic."),  # 2 s pause
        seg(5.1, 6, "x" * 700 + "."),  # long sentence closes the paragraph
        seg(6.1, 7, "After the long one."),
    ]
    paras = tt.paragraphs(segs)
    assert [p["text"] for p in paras] == [
        "Hello there. How are you?",
        "A new topic. " + "x" * 700 + ".",
        "After the long one.",
    ]
    assert [p["id"] for p in paras] == [1, 2, 3]
    assert paras[0]["start"] == 0 and paras[0]["end"] == 2


def test_paragraphs_ignore_blank_and_sort():
    paras = tt.paragraphs([seg(1.5, 2.5, "second"), seg(1, 2, "  "), seg(0, 1, "first")])
    assert [p["text"] for p in paras] == ["first second"]


def test_paragraphs_hard_cap_without_sentence_end():
    segs = [seg(i, i + 0.5, "word " * 50) for i in range(10)]  # 250 chars each, no punctuation
    paras = tt.paragraphs(segs)
    assert len(paras) == 2
    assert all(len(p["text"]) <= 1500 for p in paras)


def test_parse_paragraphs_joins_wrapped_lines():
    got = tt.parse_paragraphs("1|first half\nsecond half\n\n2|other\n9|ignored", {1, 2})
    assert got == {1: "first half second half", 2: "other"}


def test_acceptable_ratio_for_compact_scripts():
    src = "This is a fairly long English sentence that, once translated, becomes a rather short one in Japanese."
    ja = "これは日本語ではかなり短くなる、やや長めの英語の文です。"
    assert not tt.acceptable(src, ja)
    assert tt.acceptable(src, ja, min_ratio=0.2)


def test_acceptable():
    assert tt.acceptable("Bom dia a todos.", "Good morning everyone.")
    assert not tt.acceptable("Bom dia.", "")
    long = "Esta é uma frase bem longa que o modelo deveria traduzir inteira."
    assert not tt.acceptable(long, long)
    assert not tt.acceptable(long, "Short.")


def test_batches_respect_size():
    paras = [{"id": i, "text": "a" * 500} for i in range(1, 6)]
    sizes = [len(b) for b in tt.batches(paras, size=1200)]
    assert sizes == [2, 2, 1]


class FakeSegment:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


class FakeWhisper:
    calls: ClassVar[list] = []
    lines: ClassVar[list] = ["Bonjour à tous.", "Aujourd'hui on parle de Go."]

    def __init__(self, *args, **kwargs):
        pass

    def transcribe(self, wav, **kwargs):
        FakeWhisper.calls.append(kwargs)
        info = type("Info", (), {"language": "fr"})()
        return iter(
            [FakeSegment(0.5, 1.5, FakeWhisper.lines[0]), FakeSegment(4, 5, FakeWhisper.lines[1])]
        ), info


@pytest.fixture
def fake_media(monkeypatch, tmp_path):
    FakeWhisper.calls = []
    module = types.ModuleType("faster_whisper")
    module.WhisperModel = FakeWhisper
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    monkeypatch.setattr(tt, "probe_duration", lambda path: 30.0)
    monkeypatch.setattr(tt.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(tt.os, "remove", lambda p: None)
    out = tmp_path / "out"
    monkeypatch.setenv("SHOW_OUTPUT_DIR", str(out))
    media = tmp_path / "talk.mp4"
    media.write_bytes(b"video")
    return media, out / "talk transcript"


def test_end_to_end_writes_both_texts(fake_media, ollama, capsys):
    media, work = fake_media
    ollama.translate = lambda i, text: "PT " + text
    tt.main([str(media), "--about", "a Go talk"])
    assert (work / "talk.fr.txt").read_text() == "Bonjour à tous.\n\nAujourd'hui on parle de Go.\n"
    assert (work / "talk.pt-BR.txt").read_text() == (
        "PT Bonjour à tous.\n\nPT Aujourd'hui on parle de Go.\n"
    )
    system = ollama.requests[0]["body"]["messages"][0]["content"]
    assert "French" in system and "Brazilian Portuguese" in system and "a Go talk" in system
    assert FakeWhisper.calls[0]["vad_filter"] is True
    assert "FINISHED talk.mp4" in capsys.readouterr().out
    assert media.read_bytes() == b"video"


def test_resume_translates_only_missing(fake_media, ollama):
    media, work = fake_media
    work.mkdir(parents=True)
    segments = [seg(0.5, 1.5, FakeWhisper.lines[0]), seg(4, 5, FakeWhisper.lines[1])]
    state = {"offset": 30.0, "model": "medium", "language": "fr", "segments": segments}
    (work / "segments.json").write_text(json.dumps(state))
    (work / "tr.pt-BR.json").write_text(json.dumps({"1": "já feito"}))
    ollama.translate = lambda i, text: "PT " + text
    tt.main([str(media)])
    assert FakeWhisper.calls == []  # the saved transcript is reused
    assert ollama.lines_asked() == [2]
    assert (work / "talk.pt-BR.txt").read_text().startswith("já feito\n\n")


def test_new_whisper_model_drops_old_translations(fake_media, ollama):
    media, work = fake_media
    work.mkdir(parents=True)
    (work / "segments.json").write_text(
        json.dumps({"offset": 30.0, "model": "small", "segments": []})
    )
    (work / "tr.pt-BR.json").write_text(json.dumps({"1": "stale", "2": "stale"}))
    ollama.translate = lambda i, text: "PT " + text
    tt.main([str(media), "--model", "medium"])
    assert ollama.lines_asked() == [1, 2]
    assert "stale" not in (work / "talk.pt-BR.txt").read_text()


def test_output_in_new_folder(fake_media, ollama, tmp_path):
    media, _ = fake_media
    out = tmp_path / "new" / "deep" / "talk.txt"
    tt.main([str(media), "--output", str(out)])
    assert out.exists()


def test_failed_paragraph_keeps_source_and_warns(fake_media, ollama, capsys):
    media, work = fake_media
    ollama.translate = lambda i, text: None if i == 2 else "PT " + text
    tt.main([str(media)])
    out = capsys.readouterr().out
    assert "WARNING: 1 paragraphs kept in fr: [2]" in out
    assert "Aujourd'hui on parle de Go." in (work / "talk.pt-BR.txt").read_text()
    assert len(ollama.requests) == 3  # batch, retry, alone


def test_timestamps_and_transcript_only(fake_media, ollama):
    media, work = fake_media
    tt.main([str(media), "--transcript-only", "--timestamps"])
    assert (work / "talk.fr.txt").read_text().startswith("[00:00:00] Bonjour")
    assert not (work / "talk.pt-BR.txt").exists()
    assert ollama.requests == []


def test_same_language_skips_translation(fake_media, ollama, capsys):
    media, _ = fake_media
    tt.main([str(media), "--to", "fr"])
    assert ollama.requests == []
    assert "already in fr" in capsys.readouterr().out


def test_ollama_down_keeps_transcript(fake_media, monkeypatch, capsys):
    media, work = fake_media
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")
    with pytest.raises(SystemExit) as e:
        tt.main([str(media)])
    assert "TRANSLATION FAILED" in str(e.value)
    assert (work / "talk.fr.txt").exists()


def test_missing_file(tmp_path):
    with pytest.raises(SystemExit):
        tt.main([str(tmp_path / "nope.mp3")])
