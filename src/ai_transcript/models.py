from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path


class SessionStatus(str, Enum):
    READY = "ready"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


class ProcessingMode(str, Enum):
    CLEAN_TRANSCRIPT = "Clean Transcript"
    SMART_NOTES = "Smart Notes"
    SUMMARY = "Summary"
    DETAILED_NOTES = "Detailed Notes"
    CUSTOM = "Custom Prompt"


@dataclass(slots=True)
class TranscriptSession:
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    language: str
    status: SessionStatus
    duration_seconds: float
    directory: Path
    transcript_txt: Path
    transcript_md: Path
    notes_md: Path


@dataclass(slots=True)
class TranscriptSegment:
    start_seconds: float
    end_seconds: float
    text: str
    language: str | None = None


@dataclass(slots=True)
class TranscriptionResult:
    segments: list[TranscriptSegment]
    detected_language: str | None = None
    language_probability: float | None = None
