"""Local dictation: API limits and model reuse without network or microphone access."""

import io
import sys
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
