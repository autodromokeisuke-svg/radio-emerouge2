"""VOICEVOX互換API（AivisSpeech / VOICEVOX 共通）クライアント。

AivisSpeech Engine は VOICEVOX 互換の HTTP API を提供しているため、
1つのクラスで両エンジンを扱える。

流れ:
  GET  /speakers                       -> モデル名・スタイル名からスタイルIDを解決
  POST /audio_query?text&speaker=ID    -> 合成用クエリ(JSON)
  POST /synthesis?speaker=ID (body=クエリ) -> WAVバイナリ
"""
from __future__ import annotations

import io
import time
from typing import Any

import requests
from pydub import AudioSegment

from .base import TTSEngine


class VoicevoxCompatTTS(TTSEngine):
    def __init__(self, base_url: str, roles: dict[str, dict[str, str]],
                 extra_model_urls: list[str] | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.roles = roles  # {"eme": {"speaker": "...", "style": "..."}, ...}
        self.extra_model_urls = extra_model_urls or []
        self._style_ids: dict[str, int] = {}

    # ---------- 準備 ----------
    def prepare(self) -> None:
        self._install_extra_models()
        self._resolve_style_ids()

    def _install_extra_models(self) -> None:
        """AivisHub の追加モデルURLをエンジンにインストール（AivisSpeechのみ）。

        /aivm_models/install は multipart/form-data で url を受け取る
        （実機のOpenAPIスキーマで確認済み）。失敗しても警告のみで続行する
        （既定モデルで放送は可能）。
        """
        for url in self.extra_model_urls:
            try:
                r = requests.post(
                    f"{self.base_url}/aivm_models/install",
                    files={"url": (None, url)}, timeout=300,
                )
                if r.status_code >= 400:
                    print(f"[warn] モデル追加に失敗 ({r.status_code}): {url}")
                else:
                    print(f"[ok] モデル追加: {url}")
            except requests.RequestException as e:  # noqa: PERF203
                print(f"[warn] モデル追加でエラー: {url} ({e})")

    def _resolve_style_ids(self) -> None:
        speakers: list[dict[str, Any]] = requests.get(
            f"{self.base_url}/speakers", timeout=60
        ).json()
        for role, want in self.roles.items():
            sid = self._find_style_id(speakers, want["speaker"], want["style"])
            if sid is None:
                available = ", ".join(
                    f"{s['name']}({'/'.join(st['name'] for st in s['styles'])})"
                    for s in speakers
                )
                raise RuntimeError(
                    f"話者が見つからない: {want['speaker']}/{want['style']}\n"
                    f"利用可能: {available}"
                )
            self._style_ids[role] = sid
            print(f"[ok] {role} = {want['speaker']} / {want['style']} (id={sid})")

    @staticmethod
    def _find_style_id(speakers: list[dict], speaker_name: str,
                       style_name: str) -> int | None:
        for s in speakers:
            if speaker_name in s.get("name", ""):
                styles = s.get("styles", [])
                for st in styles:
                    if style_name in st.get("name", ""):
                        return st["id"]
                if styles:  # スタイル名が一致しない時は先頭スタイルで妥協
                    print(f"[warn] スタイル'{style_name}'なし。"
                          f"'{styles[0]['name']}'を使用")
                    return styles[0]["id"]
        return None

    # ---------- 合成 ----------
    def synth(self, role: str, text: str) -> AudioSegment:
        sid = self._style_ids[role]
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                q = requests.post(
                    f"{self.base_url}/audio_query",
                    params={"text": text, "speaker": sid}, timeout=120,
                )
                q.raise_for_status()
                w = requests.post(
                    f"{self.base_url}/synthesis",
                    params={"speaker": sid}, json=q.json(), timeout=300,
                )
                w.raise_for_status()
                return AudioSegment.from_file(io.BytesIO(w.content), format="wav")
            except requests.RequestException as e:
                last_err = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"合成に失敗: {text[:30]}... ({last_err})")
