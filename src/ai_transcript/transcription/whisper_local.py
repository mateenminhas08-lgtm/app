from __future__ import annotations

import logging
from typing import Any

import numpy as np

from ai_transcript.models import TranscriptionResult, TranscriptSegment
from ai_transcript.transcription.base import TranscriptionError, TranscriptionProvider

logger = logging.getLogger(__name__)


class LocalWhisperProvider(TranscriptionProvider):
    """Offline multilingual transcription powered by faster-whisper."""

    name = "Local Whisper"
    sends_audio_to_cloud = False

    def __init__(
        self,
        model_name: str = "small",
        device: str = "auto",
        compute_type: str = "auto",
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self._model: Any | None = None

    def _resolved_device(self) -> str:
        if self.device != "auto":
            return self.device
        try:
            import ctranslate2

            return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            return "cpu"

    def _resolved_compute_type(self, device: str) -> str:
        if self.compute_type != "auto":
            return self.compute_type
        return "float16" if device == "cuda" else "int8"

    def prepare(self) -> None:
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel

            device = self._resolved_device()
            compute_type = self._resolved_compute_type(device)
            logger.info(
                "Loading local Whisper model=%s device=%s compute_type=%s",
                self.model_name,
                device,
                compute_type,
            )
            self._model = WhisperModel(
                self.model_name,
                device=device,
                compute_type=compute_type,
            )
        except Exception as exc:
            raise TranscriptionError(
                f"Local Whisper model '{self.model_name}' could not be loaded. The first run "
                "needs internet access to download the model. Also check available disk/RAM."
            ) from exc

    def transcribe(
        self, audio: np.ndarray, sample_rate: int, language: str | None
    ) -> TranscriptionResult:
        if sample_rate != 16_000:
            raise TranscriptionError("Whisper audio must be resampled to 16 kHz.")
        self.prepare()
        try:
            generated, info = self._model.transcribe(
                audio.astype(np.float32, copy=False),
                language=None if not language or language == "auto" else language,
                beam_size=5,
                vad_filter=True,
                vad_parameters={
                    "min_silence_duration_ms": 450,
                    "speech_pad_ms": 200,
                },
                condition_on_previous_text=False,
                word_timestamps=False,
            )
            segments = [
                TranscriptSegment(
                    start_seconds=float(segment.start),
                    end_seconds=float(segment.end),
                    text=segment.text.strip(),
                    language=getattr(info, "language", None),
                )
                for segment in generated
                if segment.text.strip()
            ]
            return TranscriptionResult(
                segments=segments,
                detected_language=getattr(info, "language", None),
                language_probability=getattr(info, "language_probability", None),
            )
        except TranscriptionError:
            raise
        except Exception as exc:
            raise TranscriptionError(
                "Local transcription failed. Try a smaller model in Settings or close other "
                "memory-intensive applications."
            ) from exc

    def close(self) -> None:
        self._model = None
