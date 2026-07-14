"""config.yaml の tts.engine 設定からエンジン実体を組み立てるファクトリ。"""
from __future__ import annotations

from typing import Any

from .base import TTSEngine
from .elevenlabs_tts import ElevenLabsTTS
from .vv_compat import VoicevoxCompatTTS


def get_engine(tts_cfg: dict[str, Any]) -> TTSEngine:
    name = tts_cfg.get("engine", "aivis")
    if name == "aivis":
        c = tts_cfg["aivis"]
        return VoicevoxCompatTTS(
            base_url=c["base_url"],
            roles={"eme": c["eme"], "ruje": c["ruje"]},
            extra_model_urls=c.get("extra_model_urls", []),
        )
    if name == "voicevox":
        c = tts_cfg["voicevox"]
        return VoicevoxCompatTTS(
            base_url=c["base_url"],
            roles={"eme": c["eme"], "ruje": c["ruje"]},
        )
    if name == "elevenlabs":
        c = tts_cfg["elevenlabs"]
        return ElevenLabsTTS(
            model_id=c["model_id"],
            voice_ids={"eme": c["eme_voice_id"], "ruje": c["ruje_voice_id"]},
        )
    raise ValueError(f"未知のエンジン: {name}（aivis / voicevox / elevenlabs）")
