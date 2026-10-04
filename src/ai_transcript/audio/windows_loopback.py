from __future__ import annotations

import platform
from typing import Any

import numpy as np

from ai_transcript.audio.base import AudioCaptureError, AudioDevice, SystemAudioCapture


class WindowsWasapiLoopbackCapture(SystemAudioCapture):
    """Capture the Windows playback endpoint with WASAPI loopback.

    SoundCard is a small Python wrapper over Windows Core Audio. A loopback
    microphone represents exactly what is sent to a speaker/headphone endpoint;
    the physical microphone is never opened by this class.
    """

    def __init__(self) -> None:
        self._recorder_context: Any | None = None
        self._recorder: Any | None = None
        self._microphone: Any | None = None

    @staticmethod
    def _ensure_windows() -> None:
        if platform.system() != "Windows":
            raise AudioCaptureError(
                "System audio capture requires Windows 10 or Windows 11. "
                "This development machine is not running Windows."
            )

    @staticmethod
    def _soundcard() -> Any:
        try:
            import soundcard as sc

            return sc
        except Exception as exc:
            raise AudioCaptureError(
                "The Windows audio component is unavailable. Reinstall the app or run "
                "'pip install soundcard'."
            ) from exc

    @staticmethod
    def _device_id(device: Any) -> str:
        return str(
            getattr(device, "id", None)
            or getattr(device, "_id", None)
            or getattr(device, "name", "unknown")
        )

    @staticmethod
    def _is_loopback(device: Any) -> bool:
        return bool(getattr(device, "isloopback", False))

    def _loopbacks(self) -> tuple[list[Any], Any]:
        self._ensure_windows()
        sc = self._soundcard()
        try:
            default_speaker = sc.default_speaker()
            devices = [
                device
                for device in sc.all_microphones(include_loopback=True)
                if self._is_loopback(device)
            ]
        except Exception as exc:
            raise AudioCaptureError(
                "Windows audio devices could not be read. Check that Windows Audio is "
                "running and an output device is connected."
            ) from exc
        if default_speaker is None:
            raise AudioCaptureError(
                "No Windows playback device is active. Connect speakers or headphones first."
            )
        if not devices:
            # Some SoundCard versions only materialize the requested loopback endpoint.
            try:
                devices = [
                    sc.get_microphone(id=str(default_speaker.name), include_loopback=True)
                ]
            except Exception as exc:
                raise AudioCaptureError(
                    "No WASAPI loopback device was found. Select a working Windows output "
                    "device and restart the app."
                ) from exc
        return devices, default_speaker

    @staticmethod
    def _matches_speaker(loopback: Any, speaker: Any) -> bool:
        loopback_name = str(getattr(loopback, "name", "")).lower()
        speaker_name = str(getattr(speaker, "name", "")).lower()
        loopback_id = WindowsWasapiLoopbackCapture._device_id(loopback).lower()
        speaker_id = WindowsWasapiLoopbackCapture._device_id(speaker).lower()
        return (
            speaker_name in loopback_name
            or loopback_name in speaker_name
            or (speaker_id and speaker_id in loopback_id)
            or (loopback_id and loopback_id in speaker_id)
        )

    def list_devices(self) -> list[AudioDevice]:
        devices, default_speaker = self._loopbacks()
        return [
            AudioDevice(
                id=self._device_id(device),
                name=str(getattr(device, "name", "Windows output")),
                is_default=self._matches_speaker(device, default_speaker),
            )
            for device in devices
        ]

    def open(self, sample_rate: int, device_id: str = "default") -> None:
        self.close()
        devices, default_speaker = self._loopbacks()
        selected = None
        if device_id != "default":
            selected = next(
                (device for device in devices if self._device_id(device) == device_id), None
            )
        if selected is None:
            selected = next(
                (
                    device
                    for device in devices
                    if self._matches_speaker(device, default_speaker)
                ),
                devices[0],
            )
        try:
            # Do not request a single channel here. SoundCard documents a WASAPI
            # issue with single-channel recording; we downmix all endpoint channels.
            self._recorder_context = selected.recorder(samplerate=sample_rate)
            self._recorder = self._recorder_context.__enter__()
            self._microphone = selected
        except Exception as exc:
            self.close()
            raise AudioCaptureError(
                "The selected output device could not be opened for WASAPI loopback. "
                "Try the Default output in Settings, or close apps using exclusive audio mode."
            ) from exc

    def read(self, frame_count: int) -> np.ndarray:
        if self._recorder is None:
            raise AudioCaptureError("System audio capture is not open.")
        try:
            frames = np.asarray(
                self._recorder.record(numframes=frame_count), dtype=np.float32
            )
        except Exception as exc:
            raise AudioCaptureError(
                "System audio capture stopped unexpectedly. The Windows output device may "
                "have changed or disconnected."
            ) from exc
        if frames.ndim == 2:
            frames = frames.mean(axis=1)
        return np.nan_to_num(frames.reshape(-1), copy=False).astype(np.float32, copy=False)

    def close(self) -> None:
        context = self._recorder_context
        self._recorder = None
        self._recorder_context = None
        self._microphone = None
        if context is not None:
            try:
                context.__exit__(None, None, None)
            except Exception:
                pass
