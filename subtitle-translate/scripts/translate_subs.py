#!/usr/bin/env python3
"""Translate the subtitles of a video or of every video under a folder with a local Ollama model.

  translate_subs.py <video-or-folder> [--to en] [--from fr] [--show "..."] [--glossary FILE]

For each video: the embedded text subtitle track (or a sidecar file) is extracted,
translated line by line on the original timings, and written next to the video as
`<video name>.<lang>.srt`. Work files go to `~/Downloads/<name> subtitles/`.
Resumable: run the same command again and it continues where it stopped.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sublib

VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".webm", ".mov", ".ts"}
TEXT_CODECS = {"ass", "ssa", "subrip", "srt", "mov_text", "webvtt", "text"}
SIDECAR_EXT = (".srt", ".ass", ".ssa", ".vtt")
MODEL_PREFERENCE = (
    "qwen3:30b-a3b-instruct",
    "qwen3:30b",
    "qwen3:14b",
    "gemma3:27b",
    "gemma3:12b",
    "qwen3:8b",
    "gemma3:4b",
)
LANGUAGES = {
    "en": "English",
    "eng": "English",
    "fr": "French",
    "fre": "French",
    "fra": "French",
    "pt": "Portuguese",
    "por": "Portuguese",
    "pt-br": "Brazilian Portuguese",
    "pob": "Brazilian Portuguese",
    "es": "Spanish",
    "spa": "Spanish",
    "it": "Italian",
    "ita": "Italian",
    "de": "German",
    "ger": "German",
    "deu": "German",
    "ja": "Japanese",
    "jpn": "Japanese",
    "ko": "Korean",
    "kor": "Korean",
    "zh": "Chinese",
    "chi": "Chinese",
    "zho": "Chinese",
    "ru": "Russian",
    "rus": "Russian",
}
WINDOW = 20
CONTEXT = 6
RETRY_WAIT = 5  # seconds between attempts when the server does not answer


class ModelUnavailable(RuntimeError):
    """Ollama did not answer: stop instead of writing untranslated subtitles."""


SYSTEM = """You translate {source} subtitles into {target}.{show}

You receive numbered lines in the form `id|text`. Reply with the same ids in the same order, \
one line each, as `id|{target} text`. Nothing else: no notes, no blank lines, no extra ids.

Rules:
- One output line per input line. Never merge, split, skip or renumber lines, even when a \
sentence continues on the next line.
- `\\N` is a line break inside a subtitle: keep it (at most as many as the source line has).
- Keep `<i>` `</i>` tags and `[[1]]`-style placeholders exactly.
- Natural, concise subtitle {target}. Translate the meaning and the tone, not word for word.
- ALL-CAPS lines are on-screen signs: translate and keep the capitals.
- Lines with `- ` are two speakers: keep the dashes.
- Use {target} punctuation conventions.
- Keep people's names as written. In credit lines translate the role and keep the names.{glossary}"""


def language_name(code):
    return LANGUAGES.get((code or "").lower(), code or "the source language")


def output_root():
    return Path(os.environ.get("SHOW_OUTPUT_DIR") or Path.home() / "Downloads")


def ollama_url():
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    if not host.startswith("http"):
        host = "http://" + host
    return host.rstrip("/")


def find_videos(source):
    if source.is_file():
        return [source]
    videos = [p for p in source.rglob("*") if p.suffix.lower() in VIDEO_EXT and p.is_file()]
    return sorted(videos, key=lambda p: [natural(part) for part in p.relative_to(source).parts])


def natural(name):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def key_for(video, source):
    rel = video.name if source.is_file() else video.relative_to(source).as_posix()
    return re.sub(r"[/\\]", "__", str(Path(rel).with_suffix("")))


def sidecar_target(video, lang):
    return video.with_name(f"{video.stem}.{lang}.srt")


def has_target(video, lang):
    return sidecar_target(video, lang).exists()


def probe_tracks(video):
    """Subtitle tracks of a video: [{index, codec, language, title}]."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries",
         "stream=index,codec_name:stream_tags=language,title", "-of", "json", str(video)],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    if out.returncode != 0:  # not a readable video: the caller falls back to a sidecar file
        return []
    tracks = []
    for s in json.loads(out.stdout or "{}").get("streams", []):
        tags = s.get("tags", {})
        tracks.append(
            {
                "index": s["index"],
                "codec": s.get("codec_name", ""),
                "language": tags.get("language", ""),
                "title": tags.get("title", ""),
            }
        )
    return tracks


def pick_track(tracks, source_lang, track_index, target_lang):
    """The track to translate, or (None, reason)."""
    text = [t for t in tracks if t["codec"] in TEXT_CODECS]
    if track_index is not None:
        chosen = [t for t in tracks if t["index"] == track_index]
        if not chosen:
            return None, f"no subtitle track with index {track_index}"
        if chosen[0]["codec"] not in TEXT_CODECS:
            return None, f"track {track_index} is image-based ({chosen[0]['codec']})"
        return chosen[0], ""
    if source_lang:
        wanted = language_name(source_lang)
        for t in text:
            if language_name(t["language"]) == wanted or t["language"] == source_lang:
                return t, ""
        return None, f"no {wanted} text subtitle track"
    others = [t for t in text if language_name(t["language"]) != language_name(target_lang)]
    if others:
        return others[0], ""
    if tracks and not text:
        return None, "only image-based subtitle tracks (PGS/VobSub need OCR)"
    return None, "no subtitle track in another language"


def find_sidecar(video, target_lang):
    """A subtitle file next to the video that is not already in the target language."""
    for p in sorted(video.parent.glob(glob_escape(video.stem) + ".*")):
        if p.suffix.lower() not in SIDECAR_EXT:
            continue
        tags = p.name[len(video.stem) :].lower().split(".")
        if target_lang.lower() in tags:
            continue
        lang = next((t for t in tags if t in LANGUAGES), "")
        return p, lang
    return None, ""


def extract(video, track, dest):
    codec = "copy" if track["codec"] in ("ass", "ssa") else "ass"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(video),
         "-map", f"0:{track['index']}", "-c:s", codec, str(dest)],
        check=True,
    )  # fmt: skip


def convert_sidecar(sidecar, dest):
    if sidecar.suffix.lower() in (".ass", ".ssa"):
        dest.write_bytes(sidecar.read_bytes())
    else:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(sidecar), str(dest)],
            check=True,
        )


def read_lines(path):
    return path.read_text(encoding="utf-8-sig", errors="replace").splitlines()


def write_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_translations(path):
    if not path.exists():
        return {}
    return {int(k): v for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def installed_models():
    with urllib.request.urlopen(ollama_url() + "/api/tags", timeout=30) as r:
        return [m["name"] for m in json.load(r).get("models", [])]


def pick_model(requested):
    if requested:
        return requested
    if os.environ.get("SUBTITLE_MODEL"):
        return os.environ["SUBTITLE_MODEL"]
    models = installed_models()
    for wanted in MODEL_PREFERENCE:
        for m in models:
            if m.startswith(wanted):
                return m
    if not models:
        raise SystemExit("Ollama has no models installed; pull one or pass --model.")
    return models[0]


class Translator:
    def __init__(self, model, system):
        self.model, self.system = model, system
        self.tokens = 0
        self.think = True  # send "think": false until the server rejects the field

    def ask(self, user):
        body = {
            "model": self.model,
            "stream": False,
            "keep_alive": "30m",
            "messages": [
                {"role": "system", "content": self.system},
                {"role": "user", "content": user},
            ],
            "options": {"temperature": 0.2, "num_ctx": 4096, "num_predict": 1500},
        }
        for _ in range(3):
            if self.think:
                body["think"] = False
            else:
                body.pop("think", None)
            req = urllib.request.Request(
                ollama_url() + "/api/chat",
                json.dumps(body).encode(),
                {"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=900) as r:
                    data = json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 400 and self.think:
                    self.think = False
                    continue
                error = e
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                error = e
            else:
                self.tokens += data.get("eval_count", 0)
                text = data.get("message", {}).get("content", "")
                return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
            print(f"  request failed ({error}), retrying", flush=True)
            time.sleep(RETRY_WAIT)
        raise ModelUnavailable(f"Ollama at {ollama_url()} is not answering ({error})")


def parse_answer(answer, src):
    """{id: text} for the ids of `src` found in a model answer.

    A row without an id continues the line above it when that line's source has
    more rows: some answers use a real line break where `\\N` was asked for.
    """
    got, last = {}, None
    for raw in answer.splitlines():
        m = re.match(r"\s*(\d+)\s*\|(.*)$", raw)
        if m:
            last = int(m.group(1))
            if last in src and last not in got:
                got[last] = m.group(2).strip()
            else:
                last = None
        elif raw.strip() and last is not None:
            if got[last].count("\\N") < src[last].count("\\N"):
                got[last] += "\\N" + raw.strip()
            else:
                last = None
    return got


def build_prompt(rows, context):
    parts = []
    if context:
        parts.append("Previous lines, already translated (context only, do not repeat):")
        parts += [f"  {s} => {d}" for s, d in context]
        parts.append("")
    parts.append("Translate these lines:")
    parts += [f"{i}|{t}" for i, t in rows]
    return "\n".join(parts)


TRUNCATED = ("second row missing", "much shorter than the source")


def translate_rows(translator, rows, context, accept=None):
    """Translate (id, text) rows. Returns ({id: translation}, [ids left as source])."""
    src = dict(rows)
    done = {}

    def take(todo, strict):
        answer = translator.ask(build_prompt(todo, context))
        for i, t in parse_answer(answer, {i: src[i] for i, _ in todo}).items():
            c = sublib.clean(src[i], t)
            if c is None or (accept is not None and not accept(src[i], c)):
                continue
            if strict and sublib.suspicious(src[i], c) in TRUNCATED:
                continue  # looks cut off: ask again before settling for it
            done[i] = c
        return [(i, t) for i, t in todo if i not in done]

    todo = take(rows, strict=True)
    if todo:
        todo = take(todo, strict=True)
    for row in todo:  # last try: one line at a time
        take([row], strict=False)
    failed = [i for i, _ in rows if i not in done]
    return done, failed


def fill(events, translations):
    """Translations for every event; a line that has none keeps its source text."""
    return {e["id"]: translations.get(e["id"], e["text"]) for e in events}


def too_many_failed(failed, events):
    return len(failed) > max(3, len(events) // 10)


def translate_file(translator, events, tr_path):
    """Translate every event not yet in tr_path, saving after each window.

    Returns (translations, failed ids). Failed lines are not stored, so the
    next run or --recheck tries them again.
    """
    translations = load_translations(tr_path)
    failed = []
    by_id = {e["id"]: e["text"] for e in events}
    todo = [(e["id"], e["text"]) for e in events if e["id"] not in translations]
    first = todo[0][0] if todo else 0
    context = [
        (by_id[i], translations[i])
        for i in range(max(1, first - CONTEXT), first)
        if i in translations
    ]
    for start in range(0, len(todo), WINDOW):
        window = todo[start : start + WINDOW]
        done, missing = translate_rows(translator, window, context)
        failed += missing
        translations.update(done)
        write_json(tr_path, translations)
        context = [(by_id[i], translations[i]) for i, _ in window if i in translations][-CONTEXT:]
    return translations, failed


def install(lines, events, translations, work_srt, target):
    text = sublib.to_srt(sublib.apply_translations(lines, events, fill(events, translations)))
    work_srt.parent.mkdir(parents=True, exist_ok=True)
    work_srt.write_text(text, encoding="utf-8")
    target.write_text(text, encoding="utf-8")


def recheck_file(translator, events, tr_path):
    """Retry the lines that look wrong. Returns (flagged, fixed)."""
    translations = load_translations(tr_path)
    by_id = {e["id"]: e["text"] for e in events}
    flagged = [
        i for i in by_id if i not in translations or sublib.suspicious(by_id[i], translations[i])
    ]
    fixed = 0
    for i in flagged:
        context = [
            (by_id[j], translations[j]) for j in range(max(1, i - CONTEXT), i) if j in translations
        ]
        done, _ = translate_rows(
            translator,
            [(i, by_id[i])],
            context,
            accept=lambda s, d: sublib.suspicious(s, d) is None,
        )
        if i in done:
            translations[i] = done[i]
            fixed += 1
    if fixed:
        write_json(tr_path, translations)
    return len(flagged), fixed


def prepare(video, source, work, args):
    """Extract the source subtitles of one video. Returns (key, ass path or None, reason)."""
    key = key_for(video, source)
    ass = work / "src" / f"{key}.ass"
    info_path = ass.with_suffix(".json")
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
    if ass.exists() and ass.stat().st_size and same_request(info, args):
        return key, ass, ""
    ass.parent.mkdir(parents=True, exist_ok=True)
    tmp = ass.with_name(f"{key}.part.ass")
    track, reason = pick_track(probe_tracks(video), args.source_lang, args.track, args.to)
    sidecar, sidecar_lang = (None, "") if track else find_sidecar(video, args.to)
    if not track and not sidecar:
        return key, None, reason
    try:
        if track:
            extract(video, track, tmp)
            info = {"track": track["index"], "language": track["language"]}
        else:
            convert_sidecar(sidecar, tmp)
            info = {"track": None, "language": sidecar_lang, "sidecar": sidecar.name}
    except subprocess.CalledProcessError:
        tmp.unlink(missing_ok=True)
        return key, None, "ffmpeg could not read the subtitle track"
    if ass.exists():  # another track than last time: its translations no longer apply
        for old in (work / "tr").glob(f"*/{glob_escape(key)}.json"):
            old.unlink()
    tmp.replace(ass)
    write_json(info_path, info)
    return key, ass, ""


def glob_escape(text):
    return re.sub(r"([\[\]*?])", r"[\1]", text)


def same_request(info, args):
    """Does a cached source match the track/language asked for now?"""
    if not info:  # no record of how it was extracted: take it as it is
        return True
    if args.track is not None and info.get("track") != args.track:
        return False
    if args.source_lang:
        return language_name(info.get("language")) == language_name(args.source_lang)
    return True


def source_language(key, work, args):
    if args.source_lang:
        return args.source_lang
    info_path = work / "src" / f"{key}.json"
    if info_path.exists():
        return json.loads(info_path.read_text(encoding="utf-8")).get("language", "")
    return ""


def system_prompt(args, source_lang):
    show = f" They are from {args.show}." if args.show else ""
    glossary = ""
    if args.glossary:
        text = Path(args.glossary).expanduser().read_text(encoding="utf-8").strip()
        glossary = f"\n\nNames and vocabulary:\n{text}"
    return SYSTEM.format(
        source=language_name(source_lang),
        target=language_name(args.to),
        show=show,
        glossary=glossary,
    )


def print_status(videos, source, work, lang):
    done = partial = 0
    for v in videos:
        tr = work / "tr" / lang / f"{key_for(v, source)}.json"
        if has_target(v, lang):
            done += 1
        elif tr.exists():
            partial += 1
            print(f"partial  {v.name} ({len(load_translations(tr))} lines so far)")
        else:
            print(f"pending  {v.name}")
    print(
        f"{done} done, {partial} partial, {len(videos) - done - partial} pending of {len(videos)}"
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("path", help="a video file or a folder (walked recursively)")
    ap.add_argument("--to", default="en", help="target language code (default: en)")
    ap.add_argument("--from", dest="source_lang", metavar="LANG", help="source language code")
    ap.add_argument("--track", type=int, help="ffprobe stream index of the track to translate")
    ap.add_argument("--model", help="Ollama model ($SUBTITLE_MODEL; default: best installed)")
    ap.add_argument("--show", default="", help='what is being watched, e.g. "a boxing anime"')
    ap.add_argument("--glossary", help="text file with names and terms for the model")
    ap.add_argument("--name", help="name of the work folder (default: the source's name)")
    ap.add_argument("--output", help="work folder (default: ~/Downloads/<name> subtitles)")
    ap.add_argument("--only", nargs="+", default=[], help="only videos whose path contains this")
    ap.add_argument("--limit", type=int, default=0, help="translate at most N videos, then stop")
    ap.add_argument("--extract-only", action="store_true", help="extract the tracks and stop")
    ap.add_argument("--recheck", action="store_true", help="retry the lines that look wrong")
    ap.add_argument("--status", action="store_true", help="list what is done and what is left")
    ap.add_argument("--force", action="store_true", help="redo videos that already have the .srt")
    args = ap.parse_args(argv)

    source = Path(args.path).expanduser().resolve()
    if not source.exists():
        raise SystemExit(f"not found: {source}")
    name = args.name or (source.stem if source.is_file() else source.name)
    work = Path(args.output).expanduser() if args.output else output_root() / f"{name} subtitles"
    videos = find_videos(source)
    if args.only:
        videos = [v for v in videos if any(s in str(v) for s in args.only)]
    if not videos:
        raise SystemExit(f"no video files under {source}")
    if args.status:
        print_status(videos, source, work, args.to)
        return 0
    work.mkdir(parents=True, exist_ok=True)
    tr_dir = work / "tr" / args.to
    tr_dir.mkdir(parents=True, exist_ok=True)

    if args.recheck:
        selected = [v for v in videos if (tr_dir / f"{key_for(v, source)}.json").exists()]
    elif args.force or args.extract_only:
        selected = videos
    else:
        selected = [v for v in videos if not has_target(v, args.to)]
    if args.limit:
        selected = selected[: args.limit]

    jobs = []
    for v in selected:
        key, ass, reason = prepare(v, source, work, args)
        if ass is None:
            print(f"SKIP {v.name}: {reason}", flush=True)
            continue
        lines = read_lines(ass)
        events = sublib.events_from_ass(lines)
        if args.extract_only:
            srt = ass.with_suffix(".srt")
            srt.write_text(sublib.to_srt(lines), encoding="utf-8")
            print(f"{v.name}: {len(events)} lines -> {ass.name}, {srt.name}", flush=True)
            continue
        if not events:
            print(f"SKIP {v.name}: the subtitle track has no text", flush=True)
            continue
        jobs.append((v, key, lines, events))
    if args.extract_only:
        print(f"Extracted to {work / 'src'}")
        return 0
    if not jobs:
        print("Nothing to translate.")
        return 0

    translator = Translator(
        pick_model(args.model), system_prompt(args, source_language(jobs[0][1], work, args))
    )
    try:
        if args.recheck:
            return recheck(translator, jobs, tr_dir, work, args)
        return translate(translator, jobs, tr_dir, work, args)
    except ModelUnavailable as e:
        print(f"STOPPED: {e}. Finished lines are saved; run the same command to continue.")
        return 1


def recheck(translator, jobs, tr_dir, work, args):
    total_flagged = total_fixed = 0
    for v, key, lines, events in jobs:
        flagged, fixed = recheck_file(translator, events, tr_dir / f"{key}.json")
        if fixed:
            translations = load_translations(tr_dir / f"{key}.json")
            target = sidecar_target(v, args.to)
            install(lines, events, translations, work / args.to / f"{key}.srt", target)
        total_flagged += flagged
        total_fixed += fixed
        print(f"{v.name}: {flagged} flagged, {fixed} fixed", flush=True)
    print(f"DONE: {total_fixed} of {total_flagged} flagged lines re-translated")
    return 0


def translate(translator, jobs, tr_dir, work, args):
    if args.force:
        for _, key, _, _ in jobs:
            (tr_dir / f"{key}.json").unlink(missing_ok=True)
    total = sum(
        sum(1 for e in events if e["id"] not in load_translations(tr_dir / f"{key}.json"))
        for _, key, _, events in jobs
    )
    print(f"{len(jobs)} videos, {total} lines to translate with {translator.model}", flush=True)
    start, count = time.time(), 0
    for v, key, lines, events in jobs:
        tr_path = tr_dir / f"{key}.json"
        before = len(load_translations(tr_path))
        translations, failed = translate_file(translator, events, tr_path)
        count += len(events) - before
        if too_many_failed(failed, events):
            print(
                f"{v.name}: NOT INSTALLED, {len(failed)} of {len(events)} lines came back "
                "unusable. Check the model, then run the same command again.",
                flush=True,
            )
            continue
        target = sidecar_target(v, args.to)
        install(lines, events, translations, work / args.to / f"{key}.srt", target)
        elapsed = max(time.time() - start, 0.001)
        eta = elapsed / max(count, 1) * (total - count) / 3600
        print(
            f"{v.name}: {len(events)} lines, {len(failed)} untranslated | {count}/{total} | "
            f"{translator.tokens / elapsed:.0f} tok/s | {count / elapsed * 60:.0f} lines/min | "
            f"ETA {eta:.1f} h",
            flush=True,
        )
    print("DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
