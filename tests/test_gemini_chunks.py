from ai_transcript.services.gemini_service import GeminiNotesService


def test_chunker_preserves_content_and_limits_normal_paragraphs() -> None:
    text = "\n\n".join(["paragraph " + ("x" * 900) for _ in range(10)])
    chunks = GeminiNotesService._chunks(text, max_chars=2500)

    assert len(chunks) > 1
    assert all(len(chunk) <= 2500 for chunk in chunks)
    assert sum(chunk.count("paragraph") for chunk in chunks) == 10
