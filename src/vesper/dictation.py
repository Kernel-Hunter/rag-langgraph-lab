"""Local, on-demand speech recognition. Audio is never sent to a provider."""

from __future__ import annotations

import io
import threading

from vesper import providers

# A multilingual model balances accuracy, download size and CPU speed. The first
# use downloads the model; subsequent uses reuse it from Vesper's model cache.
MODEL = "small"
_lock = threading.Lock()
_model = None


def transcribe(audio: bytes) -> str:
    """Transcribe a recording with a cached Whisper model, one request at a time."""
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel

            _model = WhisperModel(MODEL, device="cpu", compute_type="int8",
                                  download_root=str(providers.HOME / "models"))
        segments, _ = _model.transcribe(io.BytesIO(audio), beam_size=5)
        return "".join(segment.text for segment in segments).strip()
