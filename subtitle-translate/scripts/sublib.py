"""Subtitle text handling for subtitle-translate: ASS in, plain lines out, SRT back.

An ASS `Dialogue:` text is reduced to what a translator needs (`simplify`), the
translation is validated against its source (`clean`), put back on the original
event (`restore`) and the whole file is written as SRT (`to_srt`).
"""

import re

TAG = re.compile(r"\{[^}]*\}")
PH = re.compile(r"\[\[(\d+)\]\]")
ITALIC_ON, ITALIC_OFF = "{\\i1}", "{\\i0}"


def simplify(text):
    """Split an ASS text into (prefix, italic, plain, placeholders).

    prefix: override tags in front of the text, put back untouched.
    italic: the whole line is italic (the tags are removed from `plain`).
    plain: text with `<i>`/`</i>` for partial italics and `[[n]]` for other inline tags.
    """
    text = text.replace("\\h", " ")
    prefix = ""
    while True:
        m = TAG.match(text)
        if not m or m.group(0) in (ITALIC_ON, ITALIC_OFF):
            break
        if m.group(0).startswith("{\\"):
            prefix += m.group(0)
        text = text[m.end() :]
    phs = []

    def repl(m):
        tag = m.group(0)
        if tag == ITALIC_ON:
            return "<i>"
        if tag == ITALIC_OFF:
            return "</i>"
        if not tag.startswith("{\\"):
            return ""  # a translator's comment, not shown on screen
        phs.append(tag)
        return f"[[{len(phs)}]]"

    text = TAG.sub(repl, text)
    text = re.sub(r"</i>(\s*\\N\s*)<i>", r"\1", text)
    text = re.sub(r"</i>(\s*)<i>", r"\1", text)
    italic = False
    m = re.fullmatch(r"<i>(.*)</i>", text)
    if m and "<i>" not in m.group(1) and "</i>" not in m.group(1):
        italic, text = True, m.group(1)
    if text.count("<i>") > text.count("</i>"):
        text += "</i>"
    return prefix, italic, text.strip(), phs


def has_words(plain):
    """True when a simplified line has something to translate."""
    return bool(re.search(r"\w", PH.sub("", plain).replace("\\N", "")))


def events_from_ass(lines):
    """The translatable events of an ASS file, numbered from 1 in file order."""
    events = []
    for n, line in enumerate(lines):
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if len(parts) < 10:
            continue
        prefix, italic, plain, phs = simplify(parts[9])
        if not has_words(plain):
            continue
        events.append(
            {
                "id": len(events) + 1,
                "line": n,
                "prefix": prefix,
                "italic": italic,
                "text": plain,
                "phs": phs,
            }
        )
    return events


def clean(src, dst):
    """Make a translation structurally valid for its source line, or return None."""
    dst = dst.strip().replace("{", "").replace("}", "")
    if not dst:
        return None
    if sorted(PH.findall(src)) != sorted(PH.findall(dst)):
        if PH.findall(src):
            return None
        dst = PH.sub("", dst)
    if ("<i>" in src) != ("<i>" in dst) or dst.count("<i>") != dst.count("</i>"):
        if "<i>" in src:
            return None
        dst = dst.replace("<i>", "").replace("</i>", "")
    dst = dst.replace("\\n", "\\N")
    while dst.count("\\N") > max(1, src.count("\\N")):
        head, _, tail = dst.rpartition("\\N")
        dst = f"{head} {tail}"
    dst = re.sub(r" +", " ", dst).strip()
    if "\\N" not in src and "\\N" in dst and len(dst) < 38:
        dst = dst.replace("\\N", " ")
    return dst or None


def restore(event, translation):
    """The ASS text of an event with its translation in place."""
    text = translation
    for i, ph in enumerate(event["phs"], 1):
        text = text.replace(f"[[{i}]]", ph)
    text = text.replace("<i>", ITALIC_ON).replace("</i>", ITALIC_OFF)
    if event["italic"]:
        text = ITALIC_ON + text + ITALIC_OFF
    return event["prefix"] + text


def apply_translations(lines, events, translations):
    """A copy of the ASS lines with every event's text replaced by its translation."""
    out = list(lines)
    for e in events:
        parts = out[e["line"]].split(",", 9)
        parts[9] = restore(e, translations[e["id"]])
        out[e["line"]] = ",".join(parts)
    return out


def srt_time(stamp):
    """ASS `H:MM:SS.cc` to SRT `HH:MM:SS,mmm`."""
    h, m, sec = stamp.strip().split(":")
    whole, _, frac = sec.partition(".")
    return f"{int(h):02d}:{int(m):02d}:{int(whole):02d},{int(frac.ljust(3, '0')[:3]):03d}"


def to_srt(lines):
    """ASS lines to SRT text: italics and top placement kept, other styling dropped."""
    italic_styles, top_styles, fmt = set(), set(), []
    events = []
    for line in lines:
        if line.startswith("Format:") and "Fontname" in line:
            fmt = [f.strip() for f in line.split(":", 1)[1].split(",")]
        elif line.startswith("Style:") and fmt:
            style = dict(zip(fmt, [f.strip() for f in line.split(":", 1)[1].split(",")]))
            if style.get("Italic") not in ("0", None):
                italic_styles.add(style["Name"])
            if style.get("Alignment") in ("7", "8", "9"):
                top_styles.add(style["Name"])
        elif line.startswith("Dialogue:"):
            parts = line.split(",", 9)
            if len(parts) < 10:
                continue
            text, style = parts[9], parts[3].strip()
            top = style in top_styles or bool(re.search(r"\\an[789](?!\d)|\\a(5|6|7)(?!\d)", text))
            text = text.replace(ITALIC_ON, "<i>").replace(ITALIC_OFF, "</i>")
            text = TAG.sub("", text).replace("\\h", " ")
            rows = [re.sub(r" +", " ", row).strip() for row in re.split(r"\\[Nn]", text)]
            text = "\n".join(row for row in rows if row)
            text = re.sub(r"</i>(\s*)<i>", r"\1", text)  # one italic span over both rows
            if not text:
                continue
            if style in italic_styles and "<i>" not in text:
                text = f"<i>{text}</i>"
            if text.count("<i>") > text.count("</i>"):
                text += "</i>"
            if top:
                text = "{\\an8}" + text
            events.append((srt_time(parts[1]), srt_time(parts[2]), text))
    events.sort(key=lambda e: (e[0], e[1]))
    return "".join(f"{n}\n{a} --> {b}\n{t}\n\n" for n, (a, b, t) in enumerate(events, 1))


def suspicious(src, dst):
    """Why a translated line deserves a second try, or None.

    Cheap signals only: the line came back unchanged although it has ordinary
    words, it lost its second row, or its length is far off the source's.
    """
    words = re.findall(r"[^\W\d_]{3,}", PH.sub("", src).replace("\\N", " "))
    if dst == src and len(words) >= 2 and any(w.islower() for w in words):
        return "unchanged"
    if "\\N" in src and "\\N" not in dst and len(dst) < 0.6 * len(src):
        return "second row missing"
    if len(dst) > 1.6 * len(src) + 15:
        return "much longer than the source"
    if len(dst) < 0.4 * len(src) - 5:
        return "much shorter than the source"
    return None
