from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from ai_transcript.models import TranscriptionResult


class TranscriptionError(RuntimeError):
    """A user-facing speech-to-text provider error."""


class TranscriptionProvider(ABC):
    """Provider boundary for local Whisper, cloud STT, or future engines."""

    name: str
    sends_audio_to_cloud: bool

    @abstractmethod
    def prepare(self) -> None:
        """Load/download resources before capture starts."""
        raise NotImplementedError

    @abstractmethod
    def transcribe(
        self, audio: np.ndarray, sample_rate: int, language: str | None
    ) -> TranscriptionResult:
        raise NotImplementedError

    def close(self) -> None:
        """Release provider resources when the pipeline stops."""
        return None
