"""Local, on-demand speech recognition. Audio is never sent to a provider."""

from __future__ import annotations

import io
import threading

from vesper import providers

# Pin the model revision so the file size and cache key used for progress stay
# correct even if the upstream repository changes. This is the large model.bin;
# the few small metadata files are not included in the displayed percentage.
MODEL = "small"
MODEL_REVISION = "536b0662742c02347bc0e980a01041f333bce120"
MODEL_BLOB = "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"
MODEL_BYTES = 483_546_902
_lock = threading.Lock()
_progress_lock = threading.Lock()
_model = None
_phase = "idle"


def _set_phase(value: str) -> None:
    global _phase
    with _progress_lock:
        _phase = value


def progress() -> dict:
    """Report the cached model file's approximate download progress, without blocking inference."""
    with _progress_lock:
        phase = _phase
    downloaded = 0
    if phase == "loading":
        blobs = providers.HOME / "models" / "models--Systran--faster-whisper-small" / "blobs"
        try:
            # Prefer the completed file. On retry the downloader can create a new
            # partial file, so report the most recently updated one, not their sum.
            downloaded = (blobs / MODEL_BLOB).stat().st_size
        except OSError:
            latest = -1
            try:
                for path in blobs.glob(f"{MODEL_BLOB}*.incomplete"):
                    try:
                        stat = path.stat()
                        if stat.st_mtime_ns > latest:
                            latest, downloaded = stat.st_mtime_ns, stat.st_size
                    except OSError:
                        pass  # the downloader may rename it between glob and stat
            except OSError:
                pass
    return {"phase": phase, "downloaded": min(downloaded, MODEL_BYTES), "total": MODEL_BYTES}


def transcribe(audio: bytes) -> str:
    """Transcribe a recording with a cached Whisper model, one request at a time."""
    global _model
    with _lock:
        try:
            if _model is None:
                _set_phase("loading")
                from faster_whisper import WhisperModel

                _model = WhisperModel(MODEL, device="cpu", compute_type="int8", revision=MODEL_REVISION,
                                      download_root=str(providers.HOME / "models"))
            _set_phase("transcribing")
            segments, _ = _model.transcribe(io.BytesIO(audio), beam_size=5)
            return "".join(segment.text for segment in segments).strip()
        finally:
            _set_phase("idle")
