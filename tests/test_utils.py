from ai_transcript.utils import format_timestamp, safe_filename


def test_format_timestamp_handles_long_recordings() -> None:
    assert format_timestamp(2 * 3600 + 3 * 60 + 4.9) == "02:03:04"


def test_safe_filename_removes_windows_reserved_characters() -> None:
    assert safe_filename('SEO: "Keyword" <Lecture>?') == "SEO_ _Keyword_ _Lecture__"
