from __future__ import annotations

import numpy as np

from asr_client.models import SAMPLE_RATE


class StatefulResampler:
    """Streaming int16 mono resampler with state preserved between callbacks."""

    def __init__(self, input_rate: int, output_rate: int = SAMPLE_RATE) -> None:
        if input_rate <= 0 or output_rate <= 0:
            raise ValueError("采样率必须大于零")
        self.input_rate = input_rate
        self.output_rate = output_rate
        self._input_count = 0
        self._output_count = 0
        self._last_sample: np.int16 | None = None

    def process(self, pcm: bytes) -> bytes:
        if self.input_rate == self.output_rate:
            return pcm
        current = np.frombuffer(pcm, dtype="<i2")
        if not len(current):
            return b""
        old_count = self._input_count
        self._input_count += len(current)
        if self._last_sample is None:
            samples = current.astype(np.float64)
            base_index = 0
        else:
            samples = np.concatenate(
                (np.asarray([self._last_sample], dtype=np.int16), current)
            ).astype(np.float64)
            base_index = old_count - 1
        max_output_index = (
            (self._input_count - 1) * self.output_rate // self.input_rate
        )
        if self._output_count > max_output_index:
            self._last_sample = current[-1]
            return b""
        output_indices = np.arange(
            self._output_count, max_output_index + 1, dtype=np.float64
        )
        source_positions = (
            output_indices * self.input_rate / self.output_rate - base_index
        )
        converted = np.interp(
            source_positions,
            np.arange(len(samples), dtype=np.float64),
            samples,
        )
        self._output_count = max_output_index + 1
        self._last_sample = current[-1]
        return np.rint(converted).clip(-32768, 32767).astype("<i2").tobytes()
