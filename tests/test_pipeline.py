import numpy as np

from ai_transcript.services.pipeline import LiveTranscriptionPipeline


def test_linear_resample_has_expected_length() -> None:
    audio = np.linspace(-1, 1, 48_000, dtype=np.float32)
    resampled = LiveTranscriptionPipeline._resample(audio, 48_000, 16_000)

    assert resampled.dtype == np.float32
    assert len(resampled) == 16_000
