"""Local dictation: API limits and model reuse without network or microphone access."""

import io
import os
import sys
import threading
import types
import wave

import pytest


def test_audio_decoder_accepts_in_memory_recordings():
    audio = pytest.importorskip('faster_whisper.audio')
    recording = io.BytesIO()
    with wave.open(recording, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b'\x00\x00' * 1600)
    samples = audio.decode_audio(io.BytesIO(recording.getvalue()))
    assert len(samples) == 1600


def test_dictation_api_is_authenticated_and_capped(client, server, monkeypatch):
    from vesper import dictation

    calls = []
    monkeypatch.setattr(dictation, "transcribe", lambda audio: calls.append(audio) or "hello")
    assert client.post('/api/dictation', content=b'audio', headers={'X-Token': ''}).status_code == 401
    assert client.post('/api/dictation', content=b'').status_code == 400
    assert client.post('/api/dictation', content=b'x' * (server.MAX_AUDIO_BYTES + 1)).status_code == 413
    assert client.post('/api/dictation', content=b'audio', headers={'Origin': 'http://evil.example'}).status_code == 403
    response = client.post('/api/dictation', content=b'audio')
    assert response.json() == {'text': 'hello'}
    assert calls == [b'audio']


def test_dictation_errors_are_safe(client, monkeypatch):
    from vesper import dictation

    def fail(_audio):
        raise RuntimeError('private details')

    monkeypatch.setattr(dictation, 'transcribe', fail)
    response = client.post('/api/dictation', content=b'audio')
    assert response.status_code == 503
    assert 'private details' not in response.text


def test_model_loads_once_and_transcribes_in_memory(home, monkeypatch):
    from vesper import dictation

    models = []

    class Model:
        def __init__(self, name, **kwargs):
            models.append((name, kwargs))

        def transcribe(self, audio, **kwargs):
            assert isinstance(audio, io.BytesIO)
            assert audio.read() == b'audio'
            assert kwargs['beam_size'] == 5
            return iter([types.SimpleNamespace(text=' Hello'), types.SimpleNamespace(text=' world.')]), None

    monkeypatch.setitem(sys.modules, 'faster_whisper', types.SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr(dictation, '_model', None)
    assert dictation.transcribe(b'audio') == 'Hello world.'
    assert dictation.transcribe(b'audio') == 'Hello world.'
    assert len(models) == 1
    assert models[0][0] == 'small'
    assert models[0][1]['device'] == 'cpu'
    assert models[0][1]['revision'] == dictation.MODEL_REVISION
    assert dictation.progress()['phase'] == 'idle'


def test_progress_during_download_and_after_completion(home, client, monkeypatch):
    from vesper import dictation

    monkeypatch.setattr(dictation.providers, 'HOME', home)
    monkeypatch.setattr(dictation, '_model', None)
    monkeypatch.setattr(dictation, 'MODEL_BYTES', 100)
    blobs = home / 'models' / 'models--Systran--faster-whisper-small' / 'blobs'
    blobs.mkdir(parents=True)
    older = blobs / f'{dictation.MODEL_BLOB}.first.incomplete'
    newer = blobs / f'{dictation.MODEL_BLOB}.second.incomplete'
    older.write_bytes(b'x' * 60)
    newer.write_bytes(b'x' * 30)
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))
    (blobs / f'{dictation.MODEL_BLOB}.unrelated').write_bytes(b'x' * 90)
    started, release = threading.Event(), threading.Event()

    class Model:
        def __init__(self, *_args, **_kwargs):
            started.set()
            assert release.wait(10)

        def transcribe(self, _audio, **_kwargs):
            return iter([types.SimpleNamespace(text=' hello')]), None

    monkeypatch.setitem(sys.modules, 'faster_whisper', types.SimpleNamespace(WhisperModel=Model))
    result = []
    thread = threading.Thread(target=lambda: result.append(dictation.transcribe(b'audio')))
    thread.start()
    try:
        assert started.wait(5)
        assert client.get('/api/dictation/progress', headers={'X-Token': ''}).status_code == 401
        assert client.get('/api/dictation/progress').json() == {
            'phase': 'loading', 'downloaded': 30, 'total': 100,
        }
        (blobs / dictation.MODEL_BLOB).write_bytes(b'x' * 105)
        assert client.get('/api/dictation/progress').json()['downloaded'] == 100
    finally:
        release.set()
        thread.join(timeout=10)
    assert not thread.is_alive()
    assert result == ['hello']
    assert client.get('/api/dictation/progress').json()['phase'] == 'idle'


def test_progress_resets_after_loading_failure(home, monkeypatch):
    from vesper import dictation

    def fail(*_args, **_kwargs):
        raise RuntimeError('download failed')

    monkeypatch.setattr(dictation, '_model', None)
    monkeypatch.setitem(sys.modules, 'faster_whisper', types.SimpleNamespace(WhisperModel=fail))
    with pytest.raises(RuntimeError):
        dictation.transcribe(b'audio')
    assert dictation.progress()['phase'] == 'idle'
