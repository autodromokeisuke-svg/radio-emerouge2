#!/usr/bin/env bash
# GitHub Pagesの公開サイトに最新回が出たかを確かめ、デプロイが止まっていたら自動で復旧する。
# 使い方は .github/workflows/daily-radio.yml の verify-pages ジョブ（と pages-verify.yml）を参照。
# 環境変数:
#   GH_TOKEN            gh CLIが使うトークン（ログには出さない）
#   REPO                owner/repo（既定: $GITHUB_REPOSITORY）
#   SITE_BASE_URL       既定: https://<owner>.github.io/<repo名>
#   VERIFY_WAIT1_SEC    復旧を試す前に普通に待つ秒数（既定360）
#   VERIFY_TOTAL_SEC    開始からの打ち切り秒数（既定1260）
#   VERIFY_INTERVAL_SEC 確認の間隔（既定20）
#   FORCE_REBUILD       trueなら最初にPagesの再ビルドを要求する（既定false）
#   VERIFY_RETRY_SEC    最新回ファイル名の取得を再試行する間隔（既定5。テスト用）
set -euo pipefail

REPO="${REPO:-${GITHUB_REPOSITORY:-}}"
if [ -z "$REPO" ]; then
  echo "[ng] REPO（または GITHUB_REPOSITORY）が未設定です"
  exit 1
fi
SITE_BASE_URL="${SITE_BASE_URL:-https://${REPO%%/*}.github.io/${REPO#*/}}"
SITE_BASE_URL="${SITE_BASE_URL%/}"
WAIT1="${VERIFY_WAIT1_SEC:-360}"
TOTAL="${VERIFY_TOTAL_SEC:-1260}"
INTERVAL="${VERIFY_INTERVAL_SEC:-20}"
RETRY_SEC="${VERIFY_RETRY_SEC:-5}"
FORCE_REBUILD="${FORCE_REBUILD:-false}"

summary() {
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    printf '%s\n' "$1" >> "$GITHUB_STEP_SUMMARY" || true
  fi
}

# 1. gh-pages上の最新回のファイル名
LATEST=""
for attempt in 1 2 3; do
  if names="$(gh api "repos/$REPO/contents/episodes?ref=gh-pages" --jq '.[].name' 2>/dev/null)"; then
    LATEST="$(printf '%s\n' "$names" | grep -E '^radio-[0-9]{8}[.]mp3$' | sort | tail -n 1 || true)"
    [ -n "$LATEST" ] && break
  fi
  [ "$attempt" -lt 3 ] && sleep "$RETRY_SEC"
done
if [ -z "$LATEST" ]; then
  echo "[ng] gh-pages の episodes/ から最新回のファイル名を取得できませんでした"
  summary "公開の確認: **最新回のファイル名を取得できませんでした**"
  exit 1
fi
echo "最新回: $LATEST（$SITE_BASE_URL）"

# 2. 公開済みか（feed.xmlは max-age=600 でキャッシュされるので ?t= を付ける）
is_published() {
  local body
  body="$(curl -fsSL --max-time 20 "$SITE_BASE_URL/feed.xml?t=$(date +%s)" 2>/dev/null)" || return 1
  case "$body" in *"$LATEST"*) ;; *) return 1 ;; esac
  curl -fsSI --max-time 20 "$SITE_BASE_URL/episodes/$LATEST" >/dev/null 2>&1
}

request_rebuild() {
  gh api -X POST "repos/$REPO/pages/builds" >/dev/null 2>&1
}

START="$(date +%s)"

# 3. 手動復旧用: 最初に再ビルドを要求
if [ "$FORCE_REBUILD" = "true" ]; then
  if request_rebuild; then
    echo "[fix] Pagesの再ビルドを要求しました（FORCE_REBUILD）"
  else
    echo "[warn] Pagesの再ビルド要求に失敗しました。確認は続けます"
  fi
fi

TRIED=0
RECOVERED=0
while :; do
  ELAPSED=$(( $(date +%s) - START ))
  if is_published; then
    echo "[ok] 公開を確認: $LATEST（${ELAPSED}秒）"
    if [ "$RECOVERED" = "1" ]; then
      summary "公開の確認: 復旧して公開を確認しました（$LATEST、${ELAPSED}秒）"
    else
      summary "公開の確認: 公開を確認しました（$LATEST、${ELAPSED}秒）"
    fi
    exit 0
  fi

  # 5. 普通に待っても出ないとき、復旧を1回だけ試す
  if [ "$TRIED" = "0" ] && [ "$ELAPSED" -ge "$WAIT1" ]; then
    if line="$(gh run list -R "$REPO" -w pages-build-deployment -L 1 \
        --json databaseId,status,conclusion \
        --jq '.[0] | "\(.databaseId) \(.status) \(.conclusion)"' 2>/dev/null)" && [ -n "$line" ]; then
      read -r RUN_ID RUN_STATUS RUN_CONCLUSION <<< "$line"
      if [ "$RUN_STATUS" != "completed" ]; then
        echo "pages-build-deployment $RUN_ID は $RUN_STATUS です。待ちます"
      elif [ "$RUN_CONCLUSION" != "success" ]; then
        TRIED=1
        if gh run rerun "$RUN_ID" --failed -R "$REPO" >/dev/null 2>&1; then
          echo "[fix] pages-build-deployment $RUN_ID（$RUN_CONCLUSION）を再実行しました"
          RECOVERED=1
        else
          echo "[warn] pages-build-deployment $RUN_ID の再実行に失敗しました。待ち続けます"
        fi
      else
        TRIED=1
        if request_rebuild; then
          echo "[fix] Pagesの再ビルドを要求しました"
          RECOVERED=1
        else
          echo "[warn] Pagesの再ビルド要求に失敗しました。待ち続けます"
        fi
      fi
    else
      echo "[warn] pages-build-deployment の実行一覧を取得できませんでした。次の周回で再試行します"
    fi
  fi

  # 6. 打ち切り
  if [ "$ELAPSED" -ge "$TOTAL" ]; then
    echo "[ng] 公開を確認できません: $LATEST"
    echo "手動で復旧するには、失敗した pages-build-deployment を探して再実行してください:"
    echo "  gh run list -R $REPO -w pages-build-deployment -L 3"
    echo "  gh run rerun <id> --failed -R $REPO"
    summary "公開の確認: **公開を確認できませんでした**（$LATEST）。\`gh run list -R $REPO -w pages-build-deployment -L 3\` で失敗した run を探し \`gh run rerun <id> --failed -R $REPO\`"
    exit 1
  fi
  sleep "$INTERVAL"
done
