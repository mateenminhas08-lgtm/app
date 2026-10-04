from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class AudioDevice:
    id: str
    name: str
    is_default: bool = False


class AudioCaptureError(RuntimeError):
    """Raised when system audio cannot be captured."""


class SystemAudioCapture(ABC):
    """Interface for replaceable system-audio capture implementations."""

    @abstractmethod
    def list_devices(self) -> list[AudioDevice]:
        raise NotImplementedError

    @abstractmethod
    def open(self, sample_rate: int, device_id: str = "default") -> None:
        raise NotImplementedError

    @abstractmethod
    def read(self, frame_count: int) -> np.ndarray:
        """Return mono float32 audio in the range -1..1."""
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError

    def __enter__(self) -> SystemAudioCapture:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
