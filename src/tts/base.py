"""TTSエンジン共通インターフェース。

役割キー("eme" / "ruje")とテキストを渡すと、pydub.AudioSegment を返す。
新しいエンジンを足す時はこのクラスを継承して synth() を実装するだけ。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from pydub import AudioSegment


class TTSEngine(ABC):
    """全エンジン共通の抽象クラス。"""

    @abstractmethod
    def synth(self, role: str, text: str) -> AudioSegment:
        """role("eme"/"ruje")の声で text を合成して AudioSegment を返す。"""
        raise NotImplementedError

    def prepare(self) -> None:
        """必要なら起動時の準備（モデル追加など）。既定では何もしない。"""
        return None
