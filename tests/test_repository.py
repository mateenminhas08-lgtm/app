from pathlib import Path

from ai_transcript.models import TranscriptSegment
from ai_transcript.storage.repository import SessionRepository


def test_session_segments_are_indexed_and_autosaved(tmp_path: Path) -> None:
    repository = SessionRepository(tmp_path / "sessions.db", tmp_path / "sessions")
    session = repository.create_session("Python Tutorial", "en")
    repository.add_segment(
        session.id,
        TranscriptSegment(134.0, 139.0, "Install Python and open the terminal.", "en"),
    )

    assert "[00:02:14]" in repository.transcript_text(session.id)
    assert "Install Python" in session.transcript_txt.read_text(encoding="utf-8")
    assert "**[00:02:14]**" in session.transcript_md.read_text(encoding="utf-8")
    repository.close()


def test_resumed_session_keeps_manual_working_copy_current(tmp_path: Path) -> None:
    repository = SessionRepository(tmp_path / "sessions.db", tmp_path / "sessions")
    session = repository.create_session("Lecture")
    repository.add_segment(session.id, TranscriptSegment(0, 2, "Original speech"))
    repository.save_edited_transcript(session.id, "Manually corrected speech")
    repository.add_segment(session.id, TranscriptSegment(3, 5, "New speech"))

    edited = (session.directory / "edited-transcript.txt").read_text(encoding="utf-8")
    assert "Manually corrected speech" in edited
    assert "New speech" in edited
    repository.close()


def test_clear_transcript_keeps_session(tmp_path: Path) -> None:
    repository = SessionRepository(tmp_path / "sessions.db", tmp_path / "sessions")
    session = repository.create_session("Lecture")
    repository.add_segment(session.id, TranscriptSegment(0, 2, "Hello"))
    repository.clear_transcript(session.id)

    assert repository.get_segments(session.id) == []
    assert repository.get_session(session.id).transcript_txt.exists()
    repository.close()
