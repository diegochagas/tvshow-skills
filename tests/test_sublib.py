import sublib
from conftest import make_ass


def test_simplify_keeps_leading_tags_as_prefix():
    prefix, italic, plain, phs = sublib.simplify("{\\an8\\pos(10,20)}Gym entrance")
    assert (prefix, italic, plain, phs) == ("{\\an8\\pos(10,20)}", False, "Gym entrance", [])


def test_simplify_whole_line_italic_drops_the_tags():
    prefix, italic, plain, _ = sublib.simplify("{\\i1}First row{\\i0}\\N{\\i1}second row{\\i0}")
    assert (prefix, italic, plain) == ("", True, "First row\\Nsecond row")


def test_simplify_partial_italic_and_inline_tags():
    _, italic, plain, phs = sublib.simplify("He said {\\i1}no{\\i0} to {\\b1}that{\\b0}")
    assert italic is False
    assert plain == "He said <i>no</i> to [[1]]that[[2]]"
    assert phs == ["{\\b1}", "{\\b0}"]


def test_simplify_drops_translator_comments():
    assert sublib.simplify("Hello{note to self} there")[2] == "Hello there"


def test_events_skip_lines_without_words():
    lines = make_ass(["Hello", "{\\p1}m 0 0 l 10 10{\\p0}", "...", "Bye"]).splitlines()
    events = sublib.events_from_ass(lines)
    assert events[0]["text"] == "Hello"
    assert [e["id"] for e in events] == list(range(1, len(events) + 1))
    assert "..." not in [e["text"] for e in events]


def test_restore_round_trip_is_identity():
    texts = [
        "{\\an8}Top sign",
        "{\\i1}Thinking{\\i0}\\N{\\i1}out loud{\\i0}",
        "He said {\\i1}no{\\i0} to {\\b1}that{\\b0}",
        "Plain line",
    ]
    lines = make_ass(texts).splitlines()
    events = sublib.events_from_ass(lines)
    same = sublib.apply_translations(lines, events, {e["id"]: e["text"] for e in events})
    assert sublib.to_srt(same) == sublib.to_srt(lines)


def test_clean_rejects_lost_placeholders_and_italics():
    assert sublib.clean("a [[1]]b[[2]]", "a b") is None
    assert sublib.clean("a <i>b</i>", "a b") is None
    assert sublib.clean("a <i>b</i>", "x <i>y") is None
    assert sublib.clean("abc", "  ") is None


def test_clean_repairs_what_it_can():
    assert sublib.clean("plain", "pl{ai}n [[3]] <i>x</i>") == "plain x"
    assert sublib.clean("one\\Ntwo", "a\\Nb\\Nc") == "a\\Nb c"
    assert sublib.clean("long source line without a break", "short\\Nline") == "short line"


def test_srt_time():
    assert sublib.srt_time("0:01:31.00") == "00:01:31,000"
    assert sublib.srt_time("1:02:03.45") == "01:02:03,450"


def test_to_srt_formats_events():
    doc = make_ass(
        [
            "Second",
            ("Inner voice", "Thought"),
            ("Sign", "Top"),
            "{\\an8}Also on top",
            "{\\i1}Half{\\i0} italic\\Ntwo rows",
            "{\\p1}m 0 0{\\p0}",
        ]
    )
    srt = sublib.to_srt(doc.splitlines())
    blocks = srt.strip().split("\n\n")
    assert blocks[0] == "1\n00:00:00,000 --> 00:00:02,500\nSecond"
    assert "<i>Inner voice</i>" in blocks[1]
    assert blocks[2].endswith("{\\an8}Sign")
    assert blocks[3].endswith("{\\an8}Also on top")
    assert blocks[4].endswith("<i>Half</i> italic\ntwo rows")
    assert "{\\p" not in srt and "m 0 0" in srt  # tags removed, text kept as written


def test_to_srt_sorts_by_start_time():
    doc = make_ass(["a", "b"]).replace("0:00:00.00,0:00:02.50", "0:00:09.00,0:00:09.50")
    srt = sublib.to_srt(doc.splitlines())
    assert srt.index("\nb\n") < srt.index("\na\n")


def test_suspicious():
    assert sublib.suspicious("Je ne sais pas", "Je ne sais pas") == "unchanged"
    assert sublib.suspicious("Takamura !", "Takamura !") is None
    assert sublib.suspicious("Oui", "Yes " * 20) == "much longer than the source"
    assert sublib.suspicious("Une très longue phrase qui continue encore", "Hm") is not None
    assert sublib.suspicious("Je ne sais pas", "I don't know") is None
    two_rows = "On se retrouve demain\\Ndevant la gare."
    assert sublib.suspicious(two_rows, "See you tomorrow") == "second row missing"
    assert sublib.suspicious(two_rows, "See you tomorrow\\nat the station") is None
    assert sublib.suspicious(two_rows, "See you tomorrow at the station") is None
