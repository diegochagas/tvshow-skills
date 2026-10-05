#!/usr/bin/env python3
"""Transcribe an audio or video file and translate the text, as a readable .txt.

  transcribe_translate.py <audio-or-video> [--to pt-BR] [--language ja] [--model medium]

Two local models, no API: faster-whisper (the repo venv) turns the speech into
text in its own language, then the Ollama model subtitle-translate uses turns
that text into the target language, paragraph by paragraph with the previous
paragraph as context. Both results are written to
`~/Downloads/<file name> transcript/`:

  <name>.<source lang>.txt   what was said, in the original language
  <name>.<to>.txt            the translation (pt-BR by default)

Resumable: the Whisper segments and every translated paragraph are saved as
they are produced; run the same command again and it continues.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "subtitle-translate" / "scripts"))
sys.path.insert(0, str(HERE.parents[1] / "subtitle-generator" / "scripts"))
import translate_subs
from gen_subs import fmt_ts, probe_duration

MEDIA_EXT = {
    ".mkv",
    ".mp4",
    ".m4v",
    ".avi",
    ".webm",
    ".mov",
    ".ts",
    ".mp3",
    ".m4a",
    ".aac",
    ".wav",
    ".flac",
    ".ogg",
    ".opus",
    ".wma",
}
CHUNK_SEC = 600  # Whisper window; the audio is cut at this size and resumed per window
PAUSE_SEC = 1.6  # a silence this long starts a new paragraph
PARAGRAPH_CHARS = 600  # a paragraph is closed at the next sentence end past this size
HARD_CHARS = 1200  # ...and no later than this, sentence end or not (speech without punctuation)
COMPACT = {"ja", "zh", "ko"}  # scripts where a faithful translation has far fewer characters
BATCH_CHARS = 1800  # paragraphs sent to the model in one request
SENTENCE_END = re.compile(r"[.!?。！？…]['\"”」』)]?$")

SYSTEM = """You translate a spoken {source} transcript into {target}.{about}

You receive numbered paragraphs in the form `id|text`. Reply with the same ids in the same \
order, one line each, as `id|{target} text`. Nothing else: no notes, no blank lines, no extra ids.

Rules:
- One output paragraph per input paragraph. Never merge, split, skip or renumber them.
- Translate everything that is said, faithfully and completely; do not summarize.
- Natural written {target}, the register of the speaker (formal talk, casual chat...). Fix \
obvious speech-recognition slips only when the intended word is clear from context.
- Keep people's names, product names and numbers as written.{glossary}"""


def output_dir(source):
    return translate_subs.output_root() / f"{source.stem} transcript"


def paragraphs(segments, pause=PAUSE_SEC, size=PARAGRAPH_CHARS, hard=HARD_CHARS):
    """Group Whisper segments into paragraphs: a long pause, a size limit at a sentence end,
    or a hard size limit when the speech has no sentence ends."""
    out, cur, start, prev_end = [], [], None, None

    def close():
        if cur:
            out.append({"start": start, "end": prev_end, "text": " ".join(cur)})
        cur.clear()

    for seg in sorted(segments, key=lambda s: s["start"]):
        text = re.sub(r"\s+", " ", seg["text"]).strip()
        if not text:
            continue
        if cur and prev_end is not None and seg["start"] - prev_end >= pause:
            close()
        if not cur:
            start = seg["start"]
        cur.append(text)
        prev_end = seg["end"]
        length = sum(len(t) for t in cur)
        if length >= hard or (length >= size and SENTENCE_END.search(text)):
            close()
    close()
    for i, p in enumerate(out, 1):
        p["id"] = i
    return out


def transcribe(media, work, args):
    """Whisper segments for the whole file, saved per window in `work/segments.json`."""
    seg_path = work / "segments.json"
    state = json.loads(seg_path.read_text(encoding="utf-8")) if seg_path.exists() else None
    duration = probe_duration(str(media))
    if state is None or state.get("model") != args.model:
        state = {"offset": 0.0, "model": args.model, "language": args.language, "segments": []}
        for old in work.glob("tr.*.json"):  # paragraph ids will change with the new transcript
            old.unlink()
    if state["offset"] >= duration:
        return state

    from faster_whisper import WhisperModel

    print(f"Loading Whisper '{args.model}' ({args.device}/{args.compute})...", flush=True)
    model = WhisperModel(
        args.model, device=args.device, compute_type=args.compute, cpu_threads=args.threads
    )
    while state["offset"] < duration:
        start = state["offset"]
        dur = min(CHUNK_SEC, duration - start)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            wav = tf.name
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(start),
                "-t",
                str(dur),
                "-i",
                str(media),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                wav,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        t0 = time.time()
        segs, info = model.transcribe(
            wav,
            language=state.get("language"),
            beam_size=args.beam,
            vad_filter=True,
            condition_on_previous_text=False,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        if not state.get("language"):
            state["language"] = getattr(info, "language", None)
        n = 0
        for s in segs:
            if s.text.strip():
                state["segments"].append(
                    {"start": s.start + start, "end": s.end + start, "text": s.text.strip()}
                )
                n += 1
        os.remove(wav)
        state["offset"] = start + dur
        seg_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        print(
            f"  {fmt_ts(start)}-{fmt_ts(start + dur)}: {n} segments ({time.time() - t0:.0f}s)"
            f"  {100 * state['offset'] / duration:.0f}%",
            flush=True,
        )
    return state


def write_txt(path, paras, texts, timestamps):
    lines = []
    for p in paras:
        text = texts.get(p["id"], p["text"])
        if timestamps:
            text = f"[{fmt_ts(p['start'])[:8]}] {text}"
        lines.append(text)
    path.write_text("\n\n".join(lines) + "\n", encoding="utf-8")


def parse_paragraphs(answer, ids):
    """{id: text} from `id|text` rows; a row without an id continues the paragraph above it."""
    got, last = {}, None
    for raw in answer.splitlines():
        m = re.match(r"\s*(\d+)\s*\|(.*)$", raw)
        if m:
            last = int(m.group(1))
            if last in ids and last not in got:
                got[last] = m.group(2).strip()
            else:
                last = None
        elif raw.strip() and last is not None:
            got[last] += " " + raw.strip()
    return got


def acceptable(src, dst, min_ratio=0.4):
    """A translated paragraph that is not empty, not the source again and not cut short."""
    dst = dst.strip()
    if not dst:
        return False
    if len(src) > 40 and dst == src:
        return False
    return len(dst) >= min_ratio * len(src)


def batches(paras, size=BATCH_CHARS):
    batch, total = [], 0
    for p in paras:
        if batch and total + len(p["text"]) > size:
            yield batch
            batch, total = [], 0
        batch.append(p)
        total += len(p["text"])
    if batch:
        yield batch


def translate_paragraphs(translator, paras, tr_path, min_ratio=0.4):
    """Translate the paragraphs still missing in `tr_path`, saving after each batch."""
    done = translate_subs.load_translations(tr_path)
    src = {p["id"]: p["text"] for p in paras}
    todo = [p for p in paras if p["id"] not in done]
    for batch in batches(todo):
        prev = batch[0]["id"] - 1
        context = [(paras[prev - 1]["text"], done[prev])] if prev >= 1 and prev in done else []
        rows = [(p["id"], p["text"]) for p in batch]
        got = {}
        for _attempt in range(2):
            answer = translator.ask(translate_subs.build_prompt(rows, context))
            for i, t in parse_paragraphs(answer, {i for i, _ in rows}).items():
                if acceptable(src[i], t, min_ratio):
                    got[i] = t.strip()
            rows = [(i, t) for i, t in rows if i not in got]
            if not rows:
                break
        for i, text in rows:  # last try, alone
            answer = translator.ask(translate_subs.build_prompt([(i, text)], context))
            t = parse_paragraphs(answer, {i}).get(i, "")
            if acceptable(text, t, min_ratio):
                got[i] = t.strip()
        done.update(got)
        translate_subs.write_json(tr_path, done)
        print(f"  translated {len(done)}/{len(paras)} paragraphs", flush=True)
    failed = [p["id"] for p in paras if p["id"] not in done]
    return done, failed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("media", help="audio or video file")
    ap.add_argument("--to", default="pt-BR", help="target language (default: pt-BR)")
    ap.add_argument("--language", help="source language code, e.g. ja (default: auto-detect)")
    ap.add_argument(
        "--model", default="medium", help="Whisper model: small, medium, large-v3 (default: medium)"
    )
    ap.add_argument("--device", default="cpu", help="Whisper device: cpu or cuda")
    ap.add_argument("--compute", default="int8", help="Whisper compute type (int8, float16...)")
    ap.add_argument("--beam", type=int, default=5)
    ap.add_argument("--threads", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--ollama-model", help="Ollama model for the translation (default: strongest)")
    ap.add_argument("--about", help='what the recording is, e.g. "a talk about Go generics"')
    ap.add_argument("--glossary", help="file with names and terms the translation must use")
    ap.add_argument("--timestamps", action="store_true", help="prefix paragraphs with [hh:mm:ss]")
    ap.add_argument("--transcript-only", action="store_true", help="skip the translation")
    ap.add_argument("--output", help="path of the translated .txt (default: in the work folder)")
    args = ap.parse_args(argv)

    media = Path(args.media).expanduser()
    if not media.is_file():
        sys.exit(f"ERROR: file not found: {media}")
    if media.suffix.lower() not in MEDIA_EXT:
        print(f"warning: {media.suffix} is not a known audio/video extension, trying anyway")
    work = output_dir(media)
    work.mkdir(parents=True, exist_ok=True)

    state = transcribe(media, work, args)
    lang = state.get("language") or "und"
    paras = paragraphs(state["segments"])
    if not paras:
        print("DONE: no speech found, nothing written")
        return
    src_txt = work / f"{media.stem}.{lang}.txt"
    write_txt(src_txt, paras, {}, args.timestamps)
    print(f"transcript: {len(paras)} paragraphs, {lang} -> {src_txt}")
    if args.transcript_only:
        print(f"FINISHED {media.name}")
        return
    if lang.lower() == args.to.lower() or lang.split("-")[0] == args.to.lower().split("-")[0]:
        print(f"DONE: the speech is already in {args.to}; see {src_txt}")
        return

    glossary = ""
    if args.glossary:
        text = Path(args.glossary).expanduser().read_text(encoding="utf-8").strip()
        glossary = f"\n\nNames and vocabulary:\n{text}"
    system = SYSTEM.format(
        source=translate_subs.language_name(lang),
        target=translate_subs.language_name(args.to),
        about=f" It is {args.about}." if args.about else "",
        glossary=glossary,
    )
    try:
        model = translate_subs.pick_model(args.ollama_model)
        print(f"Translating with {model}...", flush=True)
        translator = translate_subs.Translator(model, system)
        min_ratio = 0.2 if args.to.lower().split("-")[0] in COMPACT else 0.4
        done, failed = translate_paragraphs(
            translator, paras, work / f"tr.{args.to}.json", min_ratio
        )
    except (translate_subs.ModelUnavailable, OSError) as e:
        sys.exit(f"TRANSLATION FAILED: {e}. The transcript is in {src_txt}; run again later.")
    out = Path(args.output).expanduser() if args.output else work / f"{media.stem}.{args.to}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    write_txt(out, paras, done, args.timestamps)
    if failed:
        print(f"WARNING: {len(failed)} paragraphs kept in {lang}: {failed}")
    print(f"DONE: {len(done)}/{len(paras)} paragraphs translated -> {out}")
    print(f"FINISHED {media.name}")


if __name__ == "__main__":
    main()
