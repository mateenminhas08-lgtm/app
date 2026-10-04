from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from ai_transcript.audio.base import AudioCaptureError, SystemAudioCapture
from ai_transcript.models import TranscriptSegment
from ai_transcript.transcription.base import TranscriptionError, TranscriptionProvider

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PipelineCallbacks:
    on_state: Callable[[str, str], None] = lambda _state, _message: None
    on_segment: Callable[[TranscriptSegment], None] = lambda _segment: None
    on_level: Callable[[float], None] = lambda _level: None
    on_language: Callable[[str, float | None], None] = lambda _lang, _prob: None
    on_warning: Callable[[str], None] = lambda _message: None
    on_error: Callable[[str], None] = lambda _message: None


class LiveTranscriptionPipeline:
    """Continuously captures audio and transcribes independent short chunks.

    Capture and inference run on different threads so no system audio is lost while
    Whisper is busy. Audio is bounded in memory; if the computer cannot transcribe
    in real time, the user is warned instead of allowing unlimited memory growth.
    """

    def __init__(
        self,
        audio_capture: SystemAudioCapture,
        provider: TranscriptionProvider,
        callbacks: PipelineCallbacks,
        *,
        capture_sample_rate: int = 48_000,
        transcription_sample_rate: int = 16_000,
        chunk_seconds: float = 5.0,
        device_id: str = "default",
        language: str = "auto",
        no_audio_warning_seconds: int = 12,
        time_offset: float = 0.0,
    ) -> None:
        self.audio_capture = audio_capture
        self.provider = provider
        self.callbacks = callbacks
        self.capture_sample_rate = capture_sample_rate
        self.transcription_sample_rate = transcription_sample_rate
        self.chunk_seconds = max(2.0, min(30.0, chunk_seconds))
        self.device_id = device_id
        self.language = language
        self.no_audio_warning_seconds = no_audio_warning_seconds
        self.time_offset = max(0.0, time_offset)

        blocks_per_minute = int(60 / 0.25)
        self._audio_queue: queue.Queue[tuple[float, np.ndarray] | None] = queue.Queue(
            maxsize=blocks_per_minute * 2
        )
        self._stop_event = threading.Event()
        self._paused_event = threading.Event()
        self._capture_thread: threading.Thread | None = None
        self._worker_thread: threading.Thread | None = None
        self._started_at = 0.0
        self._running = False
        self._lock = threading.RLock()
        self._dropped_audio = False
        self._captured_until = self.time_offset

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused_event.is_set()

    @property
    def captured_until(self) -> float:
        """Absolute session time reached by the most recent WASAPI block."""
        return self._captured_until

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()
            self._paused_event.clear()
            self._started_at = time.monotonic()
            self._dropped_audio = False
            self._worker_thread = threading.Thread(
                target=self._transcription_loop,
                name="transcription-worker",
                daemon=True,
            )
            self._worker_thread.start()
        self.callbacks.on_state(
            "loading", "Loading local Whisper (the first run may download the model)…"
        )

    def pause(self) -> None:
        if self.is_running and not self.is_paused:
            self._paused_event.set()
            self.callbacks.on_state("paused", "Paused — system audio is not being captured")

    def resume(self) -> None:
        if self.is_running and self.is_paused:
            self._paused_event.clear()
            self.callbacks.on_state("recording", "Listening to Windows system audio")

    def stop(self, wait: bool = False) -> None:
        if not self.is_running:
            return
        self.callbacks.on_state("stopping", "Finishing the last audio chunk…")
        self._stop_event.set()
        self._paused_event.clear()
        # The capture thread appends the sentinel after its final audio block. This
        # preserves queued audio instead of cutting off the end of the transcript.
        if wait and self._worker_thread and threading.current_thread() != self._worker_thread:
            self._worker_thread.join(timeout=20)

    def _start_capture(self) -> None:
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            name="wasapi-loopback-capture",
            daemon=True,
        )
        self._capture_thread.start()

    def _capture_loop(self) -> None:
        block_seconds = 0.25
        block_frames = int(self.capture_sample_rate * block_seconds)
        silence_started = time.monotonic()
        silence_notified = False
        try:
            self.audio_capture.open(self.capture_sample_rate, self.device_id)
            self.callbacks.on_state("recording", "Listening to Windows system audio")
            while not self._stop_event.is_set():
                frames = self.audio_capture.read(block_frames)
                if self._stop_event.is_set():
                    break
                rms = float(np.sqrt(np.mean(np.square(frames)))) if frames.size else 0.0
                self.callbacks.on_level(min(1.0, rms * 12.0))
                if rms > 0.0005:
                    silence_started = time.monotonic()
                    silence_notified = False
                elif (
                    not silence_notified
                    and time.monotonic() - silence_started >= self.no_audio_warning_seconds
                ):
                    self.callbacks.on_warning(
                        "No system audio detected. Start playing a video, check its volume, "
                        "or select another output device in Settings."
                    )
                    silence_notified = True
                if self._paused_event.is_set():
                    continue
                block_ended_at = self.time_offset + time.monotonic() - self._started_at
                self._captured_until = block_ended_at
                item = (
                    max(self.time_offset, block_ended_at - frames.size / self.capture_sample_rate),
                    frames,
                )
                try:
                    self._audio_queue.put(item, timeout=0.1)
                except queue.Full:
                    try:
                        self._audio_queue.get_nowait()
                        self._audio_queue.put_nowait(item)
                    except queue.Empty:
                        pass
                    if not self._dropped_audio:
                        self._dropped_audio = True
                        self.callbacks.on_warning(
                            "Transcription cannot keep up with the audio. Some oldest audio was "
                            "skipped. Choose a smaller Whisper model for this computer."
                        )
        except AudioCaptureError as exc:
            self.callbacks.on_error(str(exc))
            self._stop_event.set()
        except Exception:
            logger.exception("Unexpected capture failure")
            self.callbacks.on_error("System audio capture failed unexpectedly.")
            self._stop_event.set()
        finally:
            self.audio_capture.close()
            try:
                self._audio_queue.put_nowait(None)
            except queue.Full:
                pass

    @staticmethod
    def _resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
        if source_rate == target_rate or audio.size == 0:
            return audio.astype(np.float32, copy=False)
        target_length = max(1, round(audio.size * target_rate / source_rate))
        old_positions = np.linspace(0.0, 1.0, num=audio.size, endpoint=False)
        new_positions = np.linspace(0.0, 1.0, num=target_length, endpoint=False)
        return np.interp(new_positions, old_positions, audio).astype(np.float32)

    def _transcribe_chunk(self, audio: np.ndarray, chunk_started_at: float) -> None:
        if audio.size < self.capture_sample_rate:
            return
        resampled = self._resample(
            audio, self.capture_sample_rate, self.transcription_sample_rate
        )
        result = self.provider.transcribe(
            resampled,
            self.transcription_sample_rate,
            None if self.language == "auto" else self.language,
        )
        if result.detected_language:
            self.callbacks.on_language(
                result.detected_language, result.language_probability
            )
        for segment in result.segments:
            self.callbacks.on_segment(
                TranscriptSegment(
                    start_seconds=chunk_started_at + segment.start_seconds,
                    end_seconds=chunk_started_at + segment.end_seconds,
                    text=segment.text,
                    language=segment.language,
                )
            )

    def _transcription_loop(self) -> None:
        audio_parts: list[np.ndarray] = []
        sample_count = 0
        chunk_started_at = 0.0
        previous_block_end: float | None = None
        target_samples = int(self.capture_sample_rate * self.chunk_seconds)
        try:
            self.provider.prepare()
            if self._stop_event.is_set():
                return
            # Session timestamps begin when capture starts, not while a model is
            # downloading/loading on the first run.
            self._started_at = time.monotonic()
            self._start_capture()
            while True:
                try:
                    item = self._audio_queue.get(timeout=0.25)
                except queue.Empty:
                    if self._stop_event.is_set() and (
                        self._capture_thread is None or not self._capture_thread.is_alive()
                    ):
                        break
                    continue
                if item is None:
                    if self._stop_event.is_set():
                        break
                    continue
                timestamp, frames = item
                # A pause creates a real gap in the source timeline. Never join
                # speech from opposite sides of that gap into one Whisper chunk.
                if (
                    previous_block_end is not None
                    and timestamp - previous_block_end > 0.75
                    and audio_parts
                ):
                    self._transcribe_chunk(np.concatenate(audio_parts), chunk_started_at)
                    audio_parts.clear()
                    sample_count = 0
                if not audio_parts:
                    chunk_started_at = timestamp
                audio_parts.append(frames)
                sample_count += frames.size
                previous_block_end = timestamp + frames.size / self.capture_sample_rate
                if sample_count >= target_samples:
                    audio = np.concatenate(audio_parts)
                    audio_parts.clear()
                    sample_count = 0
                    self._transcribe_chunk(audio, chunk_started_at)
            if audio_parts and not self._paused_event.is_set():
                self._transcribe_chunk(np.concatenate(audio_parts), chunk_started_at)
        except (TranscriptionError, AudioCaptureError) as exc:
            self.callbacks.on_error(str(exc))
            self._stop_event.set()
        except Exception:
            logger.exception("Unexpected transcription failure")
            self.callbacks.on_error("Transcription stopped because of an unexpected error.")
            self._stop_event.set()
        finally:
            self._stop_event.set()
            self.audio_capture.close()
            if self._capture_thread and self._capture_thread.is_alive():
                self._capture_thread.join(timeout=2)
            self.provider.close()
            with self._lock:
                self._running = False
            self.callbacks.on_level(0.0)
            self.callbacks.on_state("stopped", "Transcript saved locally")
