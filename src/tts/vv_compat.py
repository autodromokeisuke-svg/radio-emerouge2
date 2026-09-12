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
from pathlib import Path
from typing import Any

import requests
import yaml
from pydub import AudioSegment

from .base import TTSEngine

READING_DICT_PATH = Path(__file__).resolve().parent.parent.parent / "assets" / "reading_dict.yaml"


class VoicevoxCompatTTS(TTSEngine):
    supports_reading_check = True

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
        self._sync_reading_dict()

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

    def _sync_reading_dict(self) -> None:
        """assets/reading_dict.yaml の読み間違い対策リストをエンジンのユーザー
        辞書へ登録する。

        CIは毎回エンジンを新規ダウンロードして起動するため、辞書は空の状態から
        始まる。このメソッドを毎回呼ぶことだけが唯一の永続化手段（詳細は
        reading_dict.yaml冒頭のコメント参照）。

        GET /user_dict で既存登録を見て、無ければ追加(POST)、値が違えば更新
        (PUT)、一致していれば何もしない（同一セッション内での再実行や、既に
        同じ内容が入っている場合に無駄なAPI呼び出しをしないため）。
        失敗しても警告のみで続行する（放送を止めない。誤読が直らないだけで
        放送自体は成立するため）。
        """
        try:
            words = yaml.safe_load(READING_DICT_PATH.read_text(encoding="utf-8")).get("words", [])
        except (OSError, yaml.YAMLError) as e:
            print(f"[warn] reading_dict.yamlの読み込みに失敗（辞書登録をスキップ）: {e}")
            return
        if not words:
            return

        try:
            existing: dict[str, Any] = requests.get(f"{self.base_url}/user_dict", timeout=30).json()
        except requests.RequestException as e:
            print(f"[warn] ユーザー辞書の取得に失敗（辞書登録をスキップ）: {e}")
            return
        by_surface = {v.get("surface"): (uuid, v) for uuid, v in existing.items()}

        for w in words:
            surface = w["surface"]
            pronunciation = w["pronunciation"]
            accent_type = w.get("accent_type", 0)
            word_type = w.get("word_type", "PROPER_NOUN")
            params = {
                "surface": surface, "pronunciation": pronunciation,
                "accent_type": accent_type, "word_type": word_type,
                "priority": w.get("priority", 8),
            }
            try:
                if surface in by_surface:
                    uuid, cur = by_surface[surface]
                    if cur.get("pronunciation") == pronunciation and cur.get("accent_type") == accent_type:
                        continue  # 既に同じ内容が登録済み
                    r = requests.put(f"{self.base_url}/user_dict_word/{uuid}", params=params, timeout=30)
                else:
                    r = requests.post(f"{self.base_url}/user_dict_word", params=params, timeout=30)
                r.raise_for_status()
                print(f"[ok] 読み辞書登録: {surface} -> {pronunciation}")
            except requests.RequestException as e:
                print(f"[warn] 読み辞書登録に失敗: {surface} ({e})")

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
    def query(self, role: str, text: str) -> dict:
        """audio_query を呼び、合成用クエリJSONを返す（読み確認にも使う）。

        config.yaml の当該roleに "params" があれば、audio_queryの既定値
        （speedScale/intonationScale/tempoDynamicsScale/pauseLengthScale等）
        をここで上書きする。抑揚・テンポ・間の調整はこの1箇所に集約し、
        query()/synth_from_query() のどちらの経路（build_audio・reading_check・
        audition）を通っても同じ調整が効くようにする。
        """
        sid = self._style_ids[role]
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                q = requests.post(
                    f"{self.base_url}/audio_query",
                    params={"text": text, "speaker": sid}, timeout=120,
                )
                q.raise_for_status()
                query_json = q.json()
                query_json.update(self.roles[role].get("params", {}))
                return query_json
            except requests.RequestException as e:
                last_err = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"audio_queryに失敗: {text[:30]}... ({last_err})")

    def synth_from_query(self, role: str, query_json: dict) -> AudioSegment:
        """取得済みのクエリJSONから synthesis を呼び、音声を返す。"""
        sid = self._style_ids[role]
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                w = requests.post(
                    f"{self.base_url}/synthesis",
                    params={"speaker": sid}, json=query_json, timeout=300,
                )
                w.raise_for_status()
                return AudioSegment.from_file(io.BytesIO(w.content), format="wav")
            except requests.RequestException as e:
                last_err = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"synthesisに失敗: ({last_err})")

    def synth(self, role: str, text: str) -> AudioSegment:
        query_json = self.query(role, text)
        return self.synth_from_query(role, query_json)
