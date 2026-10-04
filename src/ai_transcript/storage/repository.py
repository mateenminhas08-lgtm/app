from __future__ import annotations

import os
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ai_transcript.models import SessionStatus, TranscriptSegment, TranscriptSession
from ai_transcript.utils import atomic_write_text, format_timestamp, safe_filename


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SessionRepository:
    """SQLite session index plus crash-resistant, human-readable transcript files."""

    def __init__(self, database_path: Path, sessions_dir: Path) -> None:
        self.database_path = database_path
        self.sessions_dir = sessions_dir
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.database_path, check_same_thread=False, timeout=10
        )
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    language TEXT NOT NULL DEFAULT 'auto',
                    status TEXT NOT NULL DEFAULT 'ready',
                    duration_seconds REAL NOT NULL DEFAULT 0,
                    directory TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS segments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    start_seconds REAL NOT NULL,
                    end_seconds REAL NOT NULL,
                    text TEXT NOT NULL,
                    language TEXT,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_segments_session_time
                    ON segments(session_id, start_seconds);
                """
            )

    def _row_to_session(self, row: sqlite3.Row) -> TranscriptSession:
        directory = Path(row["directory"])
        return TranscriptSession(
            id=row["id"],
            title=row["title"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            language=row["language"],
            status=SessionStatus(row["status"]),
            duration_seconds=float(row["duration_seconds"]),
            directory=directory,
            transcript_txt=directory / "transcript.txt",
            transcript_md=directory / "transcript.md",
            notes_md=directory / "notes.md",
        )

    def create_session(self, title: str | None = None, language: str = "auto") -> TranscriptSession:
        with self._lock:
            created = _now()
            session_id = str(uuid.uuid4())
            default_title = f"Session {created.astimezone().strftime('%Y-%m-%d %H-%M')}"
            clean_title = safe_filename(title or default_title)
            directory = self.sessions_dir / session_id
            directory.mkdir(parents=True, exist_ok=False)
            with self._connection:
                self._connection.execute(
                    """INSERT INTO sessions
                    (id, title, created_at, updated_at, language, status, duration_seconds, directory)
                    VALUES (?, ?, ?, ?, ?, ?, 0, ?)""",
                    (
                        session_id,
                        clean_title,
                        created.isoformat(),
                        created.isoformat(),
                        language,
                        SessionStatus.READY.value,
                        str(directory),
                    ),
                )
            session = self.get_session(session_id)
            self._rewrite_transcript_files(session)
            return session

    def get_session(self, session_id: str) -> TranscriptSession:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown session: {session_id}")
            return self._row_to_session(row)

    def list_sessions(self) -> list[TranscriptSession]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
            return [self._row_to_session(row) for row in rows]

    def rename_session(self, session_id: str, title: str) -> TranscriptSession:
        clean_title = safe_filename(title)
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (clean_title, _now().isoformat(), session_id),
            )
        session = self.get_session(session_id)
        self._rewrite_transcript_files(session)
        return session

    def set_status(self, session_id: str, status: SessionStatus) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, _now().isoformat(), session_id),
            )

    def set_language(self, session_id: str, language: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE sessions SET language = ?, updated_at = ? WHERE id = ?",
                (language, _now().isoformat(), session_id),
            )

    def set_duration(self, session_id: str, duration_seconds: float) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE sessions
                SET duration_seconds = MAX(duration_seconds, ?), updated_at = ? WHERE id = ?""",
                (max(0.0, duration_seconds), _now().isoformat(), session_id),
            )

    def add_segment(self, session_id: str, segment: TranscriptSegment) -> None:
        clean_text = " ".join(segment.text.split()).strip()
        if not clean_text:
            return
        session = self.get_session(session_id)
        timestamp = format_timestamp(segment.start_seconds)
        txt_block = f"[{timestamp}]\n{clean_text}\n\n"
        md_block = f"**[{timestamp}]**  \n{clean_text}\n\n"
        # Append and fsync every completed STT segment. At worst, a crash leaves a
        # file with one segment not indexed in SQLite; it never loses prior text.
        destinations = [
            (session.transcript_txt, txt_block),
            (session.transcript_md, md_block),
        ]
        edited_path = session.directory / "edited-transcript.txt"
        if edited_path.exists():
            # Keep a manually edited working copy crash-safe when recording resumes.
            destinations.append((edited_path, txt_block))
        for path, block in destinations:
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(block)
                handle.flush()
                os.fsync(handle.fileno())
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO segments
                (session_id, start_seconds, end_seconds, text, language)
                VALUES (?, ?, ?, ?, ?)""",
                (
                    session_id,
                    segment.start_seconds,
                    segment.end_seconds,
                    clean_text,
                    segment.language,
                ),
            )
            self._connection.execute(
                """UPDATE sessions
                SET updated_at = ?, duration_seconds = MAX(duration_seconds, ?), status = ?
                WHERE id = ?""",
                (
                    _now().isoformat(),
                    segment.end_seconds,
                    SessionStatus.RECORDING.value,
                    session_id,
                ),
            )

    def get_segments(self, session_id: str) -> list[TranscriptSegment]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT start_seconds, end_seconds, text, language FROM segments
                WHERE session_id = ? ORDER BY start_seconds, id""",
                (session_id,),
            ).fetchall()
            return [
                TranscriptSegment(
                    start_seconds=float(row["start_seconds"]),
                    end_seconds=float(row["end_seconds"]),
                    text=row["text"],
                    language=row["language"],
                )
                for row in rows
            ]

    def transcript_text(self, session_id: str, timestamps: bool = True) -> str:
        segments = self.get_segments(session_id)
        if timestamps:
            return "\n\n".join(
                f"[{format_timestamp(segment.start_seconds)}]\n{segment.text}"
                for segment in segments
            )
        return "\n".join(segment.text for segment in segments)

    def save_edited_transcript(self, session_id: str, text: str) -> None:
        """Store a manually edited view as a separate file without corrupting timed segments."""
        session = self.get_session(session_id)
        atomic_write_text(session.directory / "edited-transcript.txt", text.rstrip() + "\n")
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (_now().isoformat(), session_id),
            )

    def save_notes(self, session_id: str, notes: str) -> Path:
        session = self.get_session(session_id)
        atomic_write_text(session.notes_md, notes.rstrip() + "\n")
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (_now().isoformat(), session_id),
            )
        return session.notes_md

    def load_notes(self, session_id: str) -> str:
        path = self.get_session(session_id).notes_md
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def clear_transcript(self, session_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM segments WHERE session_id = ?", (session_id,))
            self._connection.execute(
                """UPDATE sessions SET duration_seconds = 0, status = ?, updated_at = ?
                WHERE id = ?""",
                (SessionStatus.READY.value, _now().isoformat(), session_id),
            )
        session = self.get_session(session_id)
        edited_path = session.directory / "edited-transcript.txt"
        if edited_path.exists():
            edited_path.unlink()
        self._rewrite_transcript_files(session)

    def _rewrite_transcript_files(self, session: TranscriptSession) -> None:
        segments = self.get_segments(session.id)
        txt = [f"{session.title}\n{'=' * len(session.title)}\n\n"]
        markdown = [f"# {session.title}\n\n"]
        for segment in segments:
            timestamp = format_timestamp(segment.start_seconds)
            txt.append(f"[{timestamp}]\n{segment.text}\n\n")
            markdown.append(f"**[{timestamp}]**  \n{segment.text}\n\n")
        atomic_write_text(session.transcript_txt, "".join(txt))
        atomic_write_text(session.transcript_md, "".join(markdown))

    def delete_session(self, session_id: str) -> None:
        session = self.get_session(session_id)
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        shutil.rmtree(session.directory, ignore_errors=True)

    def close(self) -> None:
        with self._lock:
            self._connection.close()
