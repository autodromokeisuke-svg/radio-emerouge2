#!/usr/bin/env bash
# 声エンジンを起動する（GitHub Actions / ローカル共用）
# 使い方: bash scripts/start_engine.sh [aivis|voicevox|elevenlabs]
set -euo pipefail

ENGINE="${1:-aivis}"

case "$ENGINE" in
  elevenlabs)
    echo "外部API型エンジンのため起動不要"
    exit 0
    ;;

  aivis)
    PORT=10101
    # バージョンを固定する。latestのまま運用すると、キャッシュ破棄（キー変更・LRU）の
    # たびに黙って新バージョンへジャンプし、声質やAPI互換性が予告なく変わってしまう。
    # アップグレードしたい時は、この値を上げる → scripts/start_engine.sh のハッシュが
    # 変わるのでキャッシュキー（.github/workflows/daily-radio.yml等）も自動的に変わり、
    # 一度だけ再ダウンロードが走る、という手順で意図的に行うこと。
    AIVIS_ENGINE_VERSION="1.2.0"
    mkdir -p .engine
    if [ ! -x .engine/engine/run ]; then
      echo "AivisSpeech Engine ${AIVIS_ENGINE_VERSION} をダウンロード中..."
      # 確認済み: gh release view --repo Aivis-Project/AivisSpeech-Engine で
      # 1.2.0 の Linux x64 アセット名は AivisSpeech-Engine-Linux-x64-1.2.0.7z.001
      # （分割7z。同梱の .7z.txt はチェックサム等でアーカイブ本体ではない）。
      gh release download "$AIVIS_ENGINE_VERSION" --repo Aivis-Project/AivisSpeech-Engine \
        --pattern "*[Ll]inux*[Xx]64*${AIVIS_ENGINE_VERSION}*.7z.001" --dir .engine/dl --clobber
      ARCHIVE="$(ls .engine/dl | head -n 1)"
      echo "取得: $ARCHIVE"
      mkdir -p .engine/engine
      case "$ARCHIVE" in
        *.tar.gz|*.tgz) tar xzf ".engine/dl/$ARCHIVE" -C .engine/engine --strip-components=1 ;;
        *.zip)          unzip -q ".engine/dl/$ARCHIVE" -d .engine/engine ;;
        *.7z|*.7z.001)  7z x -o.engine/engine ".engine/dl/$ARCHIVE" ;;
        *) echo "未対応のアーカイブ形式: $ARCHIVE"; exit 1 ;;
      esac
      # 展開直下に run が無い場合は1階層探す
      if [ ! -f .engine/engine/run ]; then
        FOUND="$(find .engine/engine -maxdepth 2 -name run -type f | head -n 1 || true)"
        [ -n "$FOUND" ] && mv "$(dirname "$FOUND")"/* .engine/engine/ || true
      fi
    fi
    chmod +x .engine/engine/run || true
    # CI限定: actions/cacheの対象に ~/.local/share/AivisSpeech-Engine を含めているため、
    # 初回実行でエンジンがuser_dict.jsonをそこへ保存すると、以降のジョブはそれをそのまま
    # 復元してしまう。すると assets/reading_dict.yaml を編集・削除しても
    # _sync_reading_dict は「既に辞書がある」と見なして再登録せず、変更が黙って無視される。
    # （確認済み: AivisSpeech-Engine 1.2.0 の get_save_dir() は
    #   user_data_dir("AivisSpeech-Engine") を返し、Linuxでは
    #   ${XDG_DATA_HOME:-$HOME/.local/share}/AivisSpeech-Engine となる。
    #   user_dict.json の保存先は voicevox_engine/user_dict/user_dict_manager.py の
    #   _USER_DICT_PATH = get_save_dir() / "user_dict.json"）
    # ローカル運用では辞書を引き継ぎたいので、GITHUB_ACTIONS=true の時のみ削除する。
    if [ "${GITHUB_ACTIONS:-}" = "true" ]; then
      rm -f "${XDG_DATA_HOME:-$HOME/.local/share}/AivisSpeech-Engine/user_dict.json"
    fi
    # 確認済み: 起動フラグ --host / --port はこれまでの全実行で問題なく動作している
    nohup .engine/engine/run --host 127.0.0.1 --port "$PORT" > engine.log 2>&1 &
    ;;

  voicevox)
    PORT=50021
    docker run -d --rm --name voicevox -p 50021:50021 \
      voicevox/voicevox_engine:cpu-0.25.2
    ;;

  *)
    echo "未知のエンジン: $ENGINE"; exit 1 ;;
esac

echo "エンジン起動待ち (port $PORT)..."
for i in $(seq 1 240); do
  if curl -sf "http://127.0.0.1:${PORT}/version" > /dev/null 2>&1; then
    # このcurlはバージョン表示・警告用だけなので、失敗してもset -euo pipefail
    # で止めずに素通りさせる（直前のcurl -sfで疎通は確認済み）
    ACTUAL_VERSION="$(curl -s "http://127.0.0.1:${PORT}/version" || true)"
    echo "エンジン準備OK ($ACTUAL_VERSION)"
    # ピン留めしたバージョンとズレていたら警告のみ（失敗させない）。
    # ズレが起きるのは主に手元の .engine キャッシュを使い回した時で、
    # 放送自体は続行できるため止める必要はない。気づけるようにログには残す。
    if [ "$ENGINE" = "aivis" ] && [ -n "${AIVIS_ENGINE_VERSION:-}" ]; then
      case "$ACTUAL_VERSION" in
        *"$AIVIS_ENGINE_VERSION"*) : ;;
        *) echo "警告: ピン留めバージョン(${AIVIS_ENGINE_VERSION})と実際のエンジン応答が異なります: $ACTUAL_VERSION" ;;
      esac
    fi
    exit 0
  fi
  sleep 2
done

echo "エンジンが起動しなかった。ログ:"
tail -n 60 engine.log 2>/dev/null || true
exit 1
