"""Voice check: Jako acts only on its owner's voice.

The owner records a few sentences once ("enrol"). From then on, every spoken command is compared with that
voiceprint and other people's voices are ignored. Everything stays on this computer: the voiceprint is a
list of numbers in data/voiceprint.json, and no audio is kept.

Uses a small speaker-recognition model (WeSpeaker ResNet34, ONNX) through sherpa-onnx.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

RATE = 16000
MODEL_PATH = Path(os.environ.get("JAKO_VOICE_MODEL", "/models/speaker.onnx"))
DEFAULT_THRESHOLD = 0.55          # how alike a command must sound to the owner's voiceprint (cosine, 0..1)
STOP_EASIER_BY = 0.1              # "stop" is one short word: give it a little more room
MIN_SPEECH_SECONDS = 0.3


def decode(b64: str) -> np.ndarray:
    """Browser audio: 16 kHz mono, 16-bit, base64."""
    raw = np.frombuffer(base64.b64decode(b64), dtype="<i2")
    return raw.astype(np.float32) / 32768.0


def speech_only(audio: np.ndarray, rate: int = RATE) -> np.ndarray:
    """Keep the loud (spoken) parts and drop the silence around them."""
    frame = int(rate * 0.03)
    n = len(audio) // frame
    if n == 0:
        return audio[:0]
    frames = audio[: n * frame].reshape(n, frame)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    keep = rms > max(0.006, rms.max() * 0.12)
    return frames[keep].reshape(-1)


class VoiceGate:
    def __init__(self, data_dir: Path, model_path: Path = MODEL_PATH, embed=None):
        self.file = Path(data_dir) / "voiceprint.json"
        self.model_path = Path(model_path)
        self._embed_fn = embed           # tests pass their own; normally the model is loaded on first use
        self._extractor = None
        self._lock = threading.Lock()
        self.print, self.threshold = self._load()

    # ---------- state
    def _load(self):
        try:
            d = json.loads(self.file.read_text())
            return np.array(d["voiceprint"], dtype=np.float32), float(d["threshold"])
        except Exception:
            return None, DEFAULT_THRESHOLD

    def available(self) -> bool:
        return self._embed_fn is not None or self.model_path.is_file()

    def enrolled(self) -> bool:
        return self.print is not None

    def status(self) -> dict:
        return {"available": self.available(), "enrolled": self.enrolled(), "threshold": round(self.threshold, 2)}

    def forget(self) -> None:
        self.file.unlink(missing_ok=True)
        self.print, self.threshold = None, DEFAULT_THRESHOLD

    # ---------- the model
    def _embed(self, audio: np.ndarray) -> np.ndarray:
        if self._embed_fn is not None:
            e = np.asarray(self._embed_fn(audio), dtype=np.float32)
        else:
            with self._lock:
                if self._extractor is None:
                    import sherpa_onnx
                    cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(self.model_path), num_threads=1)
                    self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
                s = self._extractor.create_stream()
                s.accept_waveform(RATE, audio)
                s.input_finished()
                e = np.array(self._extractor.compute(s), dtype=np.float32)
        return e / (np.linalg.norm(e) or 1.0)

    # ---------- enrol and check
    def enroll(self, clips: list[np.ndarray]) -> dict:
        embs = []
        for c in clips:
            sp = speech_only(c)
            if len(sp) >= RATE * 1.0:
                embs.append(self._embed(sp))
        if len(embs) < 2:
            return {"ok": False, "error": "I couldn't hear enough speech. Please read each sentence out loud, close to the microphone."}
        mean = np.mean(embs, axis=0)
        mean /= np.linalg.norm(mean)
        own = [float(e @ mean) for e in embs]
        # personal threshold: a bit below how alike the owner's own sentences are, kept in a sensible range
        threshold = float(np.clip(min(own) - 0.2, 0.45, 0.65))
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps({"voiceprint": [round(float(x), 6) for x in mean], "threshold": threshold}))
        self.print, self.threshold = mean.astype(np.float32), threshold
        log.info("Voice enrolled from %d sentences (self-match %s, threshold %.2f)", len(embs),
                 ", ".join(f"{x:.2f}" for x in own), threshold)
        return {"ok": True, "threshold": round(threshold, 2)}

    def check(self, audio: np.ndarray, kind: str = "command") -> dict:
        if not self.enrolled() or not self.available():
            return {"ok": True, "score": None, "reason": "not set up"}       # open until the owner records a voice
        sp = speech_only(audio)
        if len(sp) < RATE * MIN_SPEECH_SECONDS:
            return {"ok": False, "score": None, "reason": "no speech"}
        score = float(self._embed(sp) @ self.print)
        limit = self.threshold - (STOP_EASIER_BY if kind == "stop" else 0.0)
        ok = score >= limit
        log.info("Voice check (%s): score %.2f, needs %.2f -> %s", kind, score, limit, "owner" if ok else "ignored")
        return {"ok": ok, "score": round(score, 2)}
