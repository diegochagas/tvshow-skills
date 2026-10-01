import gen_subs


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
