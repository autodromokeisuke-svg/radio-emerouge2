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
    mkdir -p .engine
    if [ ! -x .engine/engine/run ]; then
      echo "AivisSpeech Engine をダウンロード中..."
      # VERIFY(1): 最新リリースの Linux CPU 版アセット名に合わせて --pattern を調整すること
      gh release download --repo Aivis-Project/AivisSpeech-Engine \
        --pattern "*[Ll]inux*[Xx]64*" --dir .engine/dl --clobber
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
    # VERIFY(2): 起動フラグは `.engine/engine/run --help` で実機確認すること
    nohup .engine/engine/run --host 127.0.0.1 --port "$PORT" > engine.log 2>&1 &
    ;;

  voicevox)
    PORT=50021
    docker run -d --rm --name voicevox -p 50021:50021 \
      voicevox/voicevox_engine:cpu-ubuntu20.04-latest
    ;;

  *)
    echo "未知のエンジン: $ENGINE"; exit 1 ;;
esac

echo "エンジン起動待ち (port $PORT)..."
for i in $(seq 1 120); do
  if curl -sf "http://127.0.0.1:${PORT}/version" > /dev/null 2>&1; then
    echo "エンジン準備OK ($(curl -s "http://127.0.0.1:${PORT}/version"))"
    exit 0
  fi
  sleep 2
done

echo "エンジンが起動しなかった。ログ:"
tail -n 60 engine.log 2>/dev/null || true
exit 1
