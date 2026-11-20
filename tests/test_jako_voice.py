"""Voice check: only the owner's voice is acted on."""
import base64

import numpy as np
from fastapi.testclient import TestClient
from types import SimpleNamespace

from jako.voice import VoiceGate, decode, speech_only, RATE


def tone(seconds, pitch, loud=0.3):
    t = np.arange(int(RATE * seconds)) / RATE
    return (loud * np.sin(2 * np.pi * pitch * t)).astype(np.float32)


def fake_embed(audio):
    """Stand-in model: the 'voice' is the main pitch of the sound."""
    spec = np.abs(np.fft.rfft(audio[:RATE]))
    e = np.zeros(8, np.float32)
    e[min(7, int(np.argmax(spec) // 60))] = 1
    return e + 0.05


def b64(audio):
    return base64.b64encode((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()).decode()


def test_open_to_everyone_until_the_owner_records_a_voice(tmp_path):
    gate = VoiceGate(tmp_path, embed=fake_embed)
    assert gate.check(tone(2, 200))["ok"] is True


def test_after_enrolling_only_the_owner_is_accepted(tmp_path):
    gate = VoiceGate(tmp_path, embed=fake_embed)
    assert gate.enroll([tone(3, 200), tone(3, 205), tone(3, 198)])["ok"]
    assert gate.check(tone(2, 202))["ok"] is True          # owner
    assert gate.check(tone(2, 420))["ok"] is False         # someone else
    assert gate.check(np.zeros(RATE * 2, np.float32))["ok"] is False   # silence is not a command
    again = VoiceGate(tmp_path, embed=fake_embed)          # remembered after a restart
    assert again.enrolled() and again.check(tone(2, 420))["ok"] is False
    again.forget()
    assert not again.enrolled() and again.check(tone(2, 420))["ok"] is True


def test_enrolling_needs_real_speech(tmp_path):
    gate = VoiceGate(tmp_path, embed=fake_embed)
    out = gate.enroll([np.zeros(RATE * 3, np.float32)] * 3)
    assert not out["ok"] and not gate.enrolled()


def test_silence_around_speech_is_dropped():
    audio = np.concatenate([np.zeros(RATE, np.float32), tone(1, 200), np.zeros(RATE, np.float32)])
    assert abs(len(speech_only(audio)) - RATE) < RATE * 0.1


def test_voice_api(tmp_path):
    from jako.server import create_app
    from jako.memory import Memory
    brain = SimpleNamespace(mem=Memory(str(tmp_path / "m.db")), cfg=SimpleNamespace(data_dir=tmp_path))
    c = TestClient(create_app(brain, voice=VoiceGate(tmp_path, embed=fake_embed)))
    assert c.get("/api/voice").json()["enrolled"] is False
    r = c.post("/api/voice/enroll", json={"clips": [b64(tone(3, 200)), b64(tone(3, 203)), b64(tone(3, 199))]}).json()
    assert r["ok"]
    assert c.post("/api/voice/check", json={"audio": b64(tone(2, 201))}).json()["ok"] is True
    assert c.post("/api/voice/check", json={"audio": b64(tone(2, 430))}).json()["ok"] is False
    assert c.delete("/api/voice").json()["enrolled"] is False
    assert np.allclose(decode(b64(tone(0.1, 200))), tone(0.1, 200), atol=1e-3)
