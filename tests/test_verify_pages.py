"""scripts/verify_pages.sh のテスト。

偽の gh / curl（bashスクリプト）をPATHの先頭に置き、呼ばれた引数を記録して検査する。
実際のGitHubや公開サイトには一切アクセスしない。
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "verify_pages.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash が無い環境ではスキップ")

FAKE_GH = r'''#!/usr/bin/env bash
echo "gh $*" >> "$FAKE_LOG"
count() { # 呼び出し回数を数える（1始まりで返す）
  local f="$FAKE_DIR/cnt_$1" n=0
  [ -f "$f" ] && n="$(cat "$f")"
  n=$((n + 1)); echo "$n" > "$f"; echo "$n"
}
case "$*" in
  "api repos/"*"contents/episodes"*)
    n="$(count names)"
    if [ "$n" -le "${FAKE_NAMES_FAIL:-0}" ]; then exit 1; fi
    printf '%s\n' "${FAKE_NAMES:-radio-20261110.mp3}"
    ;;
  "api -X POST "*)
    if [ "${FAKE_POST_FAIL:-0}" = "1" ]; then exit 1; fi
    echo '{}'
    ;;
  "run list "*)
    if [ "${FAKE_RUNLIST_FAIL:-0}" = "1" ]; then exit 1; fi
    n="$(count runs)"
    line="$(printf '%s\n' "$FAKE_RUNS" | sed -n "${n}p")"
    [ -z "$line" ] && line="$(printf '%s\n' "$FAKE_RUNS" | tail -n 1)"
    echo "$line"
    ;;
  "run rerun "*)
    if [ "${FAKE_RERUN_FAIL:-0}" = "1" ]; then exit 1; fi
    exit 0
    ;;
  *) echo "unexpected gh call: $*" >&2; exit 99 ;;
esac
'''

FAKE_CURL = r'''#!/usr/bin/env bash
echo "curl $*" >> "$FAKE_LOG"
fixed() { grep -qE '^gh (run rerun|api -X POST)' "$FAKE_LOG"; }
published() {
  case "${FAKE_PUBLISH:-always}" in
    always) return 0 ;;
    never) return 1 ;;
    after_fix) fixed ;;
  esac
}
case "$*" in
  *feed.xml*)
    published || { echo "<rss>old</rss>"; exit 0; }
    echo "<rss>${FAKE_LATEST:-radio-20261110.mp3}</rss>"
    ;;
  *episodes/*)
    published || exit 22
    ;;
  *) exit 99 ;;
esac
'''


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def run_verify(tmp_path, **env):
    fake = tmp_path / "fakebin"
    fake.mkdir(exist_ok=True)
    _write(fake / "gh", FAKE_GH)
    _write(fake / "curl", FAKE_CURL)
    # Linuxでは実行権限が無いとPATH上で無視され、本物のgh/curlが呼ばれるため付与する
    (fake / "gh").chmod(0o755)
    (fake / "curl").chmod(0o755)
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")
    e = os.environ.copy()
    e["PATH"] = str(fake) + os.pathsep + e.get("PATH", "")
    # Git Bash(Windows)は起動時に /mingw64/bin を PATH の先頭へ足し、本物の curl が偽物より先に
    # 解決されてしまう（実ネットワークに出る）。bash 起動後に偽物のディレクトリを先頭へ戻す。
    benv = tmp_path / "bash_env.sh"
    _write(
        benv,
        f'p="{fake.as_posix()}"\n'
        'command -v cygpath >/dev/null 2>&1 && p="$(cygpath -u "$p")"\n'
        'export PATH="$p:$PATH"\n',
    )
    e["BASH_ENV"] = benv.as_posix()
    e.update(
        {
            "REPO": "owner/repo",
            "FAKE_LOG": log.as_posix(),
            "FAKE_DIR": tmp_path.as_posix(),
            "VERIFY_WAIT1_SEC": "0",
            # 公開されて終わる場面が実時間で打ち切られないよう十分大きく（公開を確認した時点で即終了する）
            "VERIFY_TOTAL_SEC": "120",
            "VERIFY_INTERVAL_SEC": "0",
            "VERIFY_RETRY_SEC": "0",
            "GH_TOKEN": "dummy",
            "LC_ALL": "C.UTF-8",  # Windows(Git Bash)で日本語出力が文字コード変換されないように
        }
    )
    e.update({k: str(v) for k, v in env.items()})
    proc = subprocess.run(
        [BASH, SCRIPT.as_posix()], env=e, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180
    )
    calls = log.read_text(encoding="utf-8").splitlines()
    return proc, calls


def count(calls, prefix):
    return sum(1 for c in calls if c.startswith(prefix))


RERUN = "gh run rerun"
POST = "gh api -X POST repos/owner/repo/pages/builds"


def test_already_published(tmp_path):
    proc, calls = run_verify(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[ok] 公開を確認: radio-20261110.mp3" in proc.stdout
    assert count(calls, RERUN) == 0
    assert count(calls, POST) == 0
    # feed.xml には ?t= を付けている
    assert any("feed.xml?t=" in c for c in calls)
    assert any("episodes/radio-20261110.mp3" in c for c in calls)


def test_failed_run_is_rerun_once(tmp_path):
    proc, calls = run_verify(
        tmp_path, FAKE_PUBLISH="after_fix", FAKE_RUNS="555 completed failure"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert count(calls, RERUN) == 1
    assert "gh run rerun 555 --failed -R owner/repo" in calls
    assert count(calls, POST) == 0
    assert "[fix] pages-build-deployment 555（failure）を再実行しました" in proc.stdout


def test_success_run_but_unpublished_requests_rebuild(tmp_path):
    proc, calls = run_verify(
        tmp_path, FAKE_PUBLISH="after_fix", FAKE_RUNS="777 completed success"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert count(calls, POST) == 1
    assert count(calls, RERUN) == 0
    assert "[fix] Pagesの再ビルドを要求しました" in proc.stdout


def test_in_progress_run_is_left_alone_then_rerun(tmp_path):
    proc, calls = run_verify(
        tmp_path,
        FAKE_PUBLISH="after_fix",
        FAKE_RUNS="900 in_progress \n900 in_progress \n900 completed failure",
        VERIFY_INTERVAL_SEC="0",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert count(calls, RERUN) == 1
    first_rerun = next(i for i, c in enumerate(calls) if c.startswith(RERUN))
    # rerun より前に run list が3回呼ばれている（実行中の間は何もしない）
    assert count(calls[:first_rerun], "gh run list") == 3
    assert count(calls, POST) == 0


def test_never_published_fails_with_single_recovery(tmp_path):
    proc, calls = run_verify(
        tmp_path,
        FAKE_PUBLISH="never",
        FAKE_RUNS="555 completed failure",
        VERIFY_TOTAL_SEC="0",  # 最初の周回で復旧を1回試して打ち切り（実時間に依存しない）
        VERIFY_INTERVAL_SEC="0",
    )
    assert proc.returncode == 1
    assert count(calls, RERUN) == 1
    assert count(calls, POST) == 0
    assert "[ng] 公開を確認できません: radio-20261110.mp3" in proc.stdout
    assert "gh run list -R owner/repo -w pages-build-deployment -L 3" in proc.stdout
    assert "gh run rerun <id> --failed -R owner/repo" in proc.stdout


def test_failed_recovery_is_not_retried(tmp_path):
    proc, calls = run_verify(
        tmp_path,
        FAKE_PUBLISH="never",
        FAKE_RUNS="555 completed failure",
        FAKE_RERUN_FAIL="1",
        VERIFY_TOTAL_SEC="0",
        VERIFY_INTERVAL_SEC="0",
    )
    assert proc.returncode == 1
    assert count(calls, RERUN) == 1
    assert "[warn]" in proc.stdout


def test_force_rebuild_posts_first(tmp_path):
    proc, calls = run_verify(tmp_path, FORCE_REBUILD="true")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    gh_calls = [c for c in calls if c.startswith("gh ")]
    assert gh_calls[0].startswith("gh api repos/owner/repo/contents/episodes")
    assert POST in gh_calls
    assert gh_calls.index(POST) == 1
    assert not any(c.startswith("curl") for c in calls[: calls.index(POST)])


def test_latest_name_lookup_fails_three_times(tmp_path):
    proc, calls = run_verify(tmp_path, FAKE_NAMES_FAIL="3")
    assert proc.returncode == 1
    assert "[ng]" in proc.stdout
    assert count(calls, "gh api repos/owner/repo/contents/episodes") == 3
    assert count(calls, "curl") == 0


def test_picks_latest_date_among_mp3_names(tmp_path):
    names = "feed.xml\nradio-20261109.mp3\nradio-20260901.mp3\nradio-20261110.mp3\nradio-20261108.mp3\nnotes.txt"
    proc, calls = run_verify(tmp_path, FAKE_NAMES=names, FAKE_LATEST="radio-20261110.mp3")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "radio-20261110.mp3" in proc.stdout
    assert any("episodes/radio-20261110.mp3" in c for c in calls)
