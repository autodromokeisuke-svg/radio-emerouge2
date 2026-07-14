"""ElevenLabs Text-to-Speech クライアント。

環境変数 ELEVENLABS_API_KEY が必要（GitHub Actions では Secrets から注入）。
config.yaml の eme_voice_id / ruje_voice_id に Voice Design 等で作った
声のIDを設定して使う。
"""
from __future__ import annotations

import io
import os
import time

import requests
from pydub import AudioSegment

from .base import TTSEngine

API_BASE = "https://api.elevenlabs.io/v1"


class ElevenLabsTTS(TTSEngine):
    def __init__(self, model_id: str, voice_ids: dict[str, str]) -> None:
        self.model_id = model_id
        self.voice_ids = voice_ids  # {"eme": "...", "ruje": "..."}
        self.api_key = os.environ.get("ELEVENLABS_API_KEY", "")

    def prepare(self) -> None:
        if not self.api_key:
            raise RuntimeError(
                "ELEVENLABS_API_KEY が未設定。"
                "`gh secret set ELEVENLABS_API_KEY` で登録してね。"
            )
        missing = [r for r, v in self.voice_ids.items() if not v]
        if missing:
            raise RuntimeError(
                f"config.yaml の voice_id が未設定: {missing}"
            )

    def synth(self, role: str, text: str) -> AudioSegment:
        voice_id = self.voice_ids[role]
        url = f"{API_BASE}/text-to-speech/{voice_id}"
        headers = {"xi-api-key": self.api_key, "Content-Type": "application/json"}
        payload = {"text": text, "model_id": self.model_id}
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                r = requests.post(url, headers=headers, json=payload, timeout=300)
                r.raise_for_status()
                return AudioSegment.from_file(io.BytesIO(r.content), format="mp3")
            except requests.RequestException as e:
                last_err = e
                time.sleep(3 * (attempt + 1))
        raise RuntimeError(f"ElevenLabs合成に失敗: {text[:30]}... ({last_err})")
