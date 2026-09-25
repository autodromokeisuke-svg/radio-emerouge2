"""読み検証パイプライン（src/reading_check.py + build_audio._run_reading_check_with_report）の
再現率とコストを、実際のAivisSpeechエンジン + 実際のClaude APIで測るための評価ツール。

ローカルPCにはANTHROPIC_API_KEYが無いため、実測はCI（.github/workflows/reading-eval.yml、
workflow_dispatchで手動実行）で行う想定。tests/fixtures/reading_eval/*.json に、実際の放送で
見つかった誤読を正解ラベルとして持たせてあるので、それをそのまま使う。

使い方:
  python tools/reading_eval.py --models claude-sonnet-4-6,claude-haiku-4-5

  1) 先にエンジンを起動しておく（AivisSpeechなら127.0.0.1:10101）
  2) --fixtures で指定したディレクトリ（既定: tests/fixtures/reading_eval）の
     各JSONについて、指摘フェーズ前の「現状の誤読」をラベルと突き合わせてベースラインとし、
     モデルごとに読み検証パイプラインを実走させて、ラベルのうち何件直ったか（再現率）・
     ラベルに無い行への修正（人間がFP/ラベル漏れを判断する材料）・トークン数・概算コストを出す
  3) 結果はMarkdownでstdoutに出し、GITHUB_STEP_SUMMARYが設定されていればそこにも追記する
     （何かを配信・公開する処理は一切行わない）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from src import reading_check as reading_check_mod  # noqa: E402
from src.build_audio import (  # noqa: E402
    _fix_known_misreadings,
    _run_reading_check_with_report,
)
from src.reading_check import extract_reading, kana_contains  # noqa: E402
from src.reading_normalize import normalize_for_tts  # noqa: E402
from src.tts import get_engine  # noqa: E402

# 100万トークンあたりの (入力$, 出力$)。前方一致で判定する
# （日付サフィックス付きのモデルIDでも拾えるように、短い名前を先に置かない）。
_PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def _price_for(model: str) -> tuple[float, float] | None:
    for prefix, price in _PRICE_PER_MTOK.items():
        if model.startswith(prefix):
            return price
    return None


def _prepare(text: str) -> str:
    """build_audio.synthesize() と同じ前処理（本番と条件を揃えるため）。"""
    return _fix_known_misreadings(normalize_for_tts(text))


DICT_CHECKS_FILENAME = "dict_checks.json"


def _load_fixtures(fixtures_dir: Path) -> list[tuple[str, dict[str, Any]]]:
    fixtures = []
    for path in sorted(fixtures_dir.glob("*.json")):
        if path.name == DICT_CHECKS_FILENAME:
            continue  # 台本フィクスチャではなく辞書チェック専用（_load_dict_checks側で読む）
        fixtures.append((path.stem, json.loads(path.read_text(encoding="utf-8"))))
    return fixtures


def _load_dict_checks(fixtures_dir: Path) -> list[dict[str, Any]]:
    """assets/reading_dict.yaml の各エントリの修正文・巻き込みチェック文
    （tests/fixtures/reading_eval/dict_checks.json）を読み込む。無ければ空。"""
    path = fixtures_dir / DICT_CHECKS_FILENAME
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _describe_exception(e: Exception) -> str:
    """例外を、台本の行やURLを含まない短い説明に変換する（型名 + 分かればHTTPステータス）。

    vv_compat.pyのengine.query()はaudio_query失敗時にRuntimeErrorで包み直すため、
    元のrequests例外はe自身ではなく暗黙の例外チェーン（__cause__/__context__）に
    入っている場合がある。両方を辿ってHTTPステータスを探す。
    """
    def _status_of(exc: Exception | None) -> int | None:
        resp = getattr(exc, "response", None)
        return getattr(resp, "status_code", None)

    status = _status_of(e)
    if status is None:
        status = _status_of(e.__cause__) or _status_of(e.__context__)
    if status is not None:
        return f"{type(e).__name__}(status={status})"
    return type(e).__name__


def run_dict_checks(engine: Any, checks: list[dict[str, Any]], role: str = "eme") -> list[dict[str, Any]]:
    """dict_checks.jsonの各項目を実際にエンジンへ問い合わせ、読みに
    must_containが（長音表記ゆれを吸収した上で）含まれるかを確認する。
    LLM呼び出しは一切行わない（辞書登録だけで直る/壊れないかの確認なので）。

    項目に "roles"（例: ["eme", "ruje"]）があれば話者ごとに実行し、結果を
    話者別に分ける。無ければ従来どおり既定話者（role引数）のみで実行する。
    1件のengine.query失敗（本番で実際に起きたaudio_queryの500など）が
    全体を落とさないよう、項目ごとに例外を捕まえて passed=False として記録する
    （エラー内容は例外の型とHTTPステータスのみ。台本本文やURLは含めない）。

    戻り値は各チェックに {"role": str, "passed": bool, "reading": str, "error": str} を
    足したリスト（"roles"指定時は同じチェック内容が話者数だけ複製される）。
    """
    results = []
    for check in checks:
        roles = check.get("roles") or [role]
        for r in roles:
            try:
                q = engine.query(r, check["text"])
                reading = extract_reading(q)
                passed = kana_contains(reading, check["must_contain"])
                results.append({**check, "role": r, "passed": passed, "reading": reading, "error": ""})
            except Exception as e:  # noqa: BLE001 - 1件の失敗で他の項目の検証を止めない
                results.append({**check, "role": r, "passed": False, "reading": "",
                                "error": _describe_exception(e)})
    return results


def _baseline_misread_indices(engine: Any, lines: list[dict[str, str]],
                              prepared_texts: list[str], expected: list[dict[str, Any]]) -> set[int]:
    """指摘フェーズ前、現状のエンジンで各ラベルが実際に誤読されているかを確認する。

    ラベル（expected）のうち、既に正しく読めているものは「再現率」の分母から
    除く（読み検証が直したかどうかを測る指標なので、元々直っていたものは対象外）。
    戻り値は誤読されているラベルのindex（expectedリスト内の位置）集合。
    """
    readings = []
    for i, text in enumerate(prepared_texts):
        q = engine.query(lines[i]["speaker"], text)
        readings.append(extract_reading(q))

    misread: set[int] = set()
    for idx, item in enumerate(expected):
        pos = item["line"] - 1
        if not (0 <= pos < len(readings)):
            continue
        if not kana_contains(readings[pos], item["correct"]):
            misread.add(idx)
    return misread


def run_fixture(engine: Any, name: str, data: dict[str, Any], models: list[str]) -> dict[str, Any]:
    lines = data["lines"]
    expected = data["expected"]
    prepared_texts = [_prepare(ln["text"]) for ln in lines]

    baseline_misread = _baseline_misread_indices(engine, lines, prepared_texts, expected)
    print(f"[{name}] 台本{len(lines)}行 / ラベル{len(expected)}件 / 現状誤読{len(baseline_misread)}件")

    expected_lines = {item["line"] for item in expected}
    per_model: dict[str, Any] = {}
    for model in models:
        reading_check_mod.last_usage = None
        queries, report = _run_reading_check_with_report(engine, lines, prepared_texts, model)

        # 行ごとに「その行へパイプラインが実際にapplied判定した指摘があるか」を
        # 引けるようにしておく（「たまたま読みがcorrectを含んでいた」だけでは
        # "fixed"扱いにしない。パイプラインが実際に修正を適用した行に限る）。
        applied_lines = {d["index"] for d in report["details"] if d["outcome"] == "applied"}

        fixed = 0
        for idx in baseline_misread:
            item = expected[idx]
            pos = item["line"] - 1
            if item["line"] not in applied_lines:
                continue
            q = queries[pos]
            reading = extract_reading(q) if q is not None else ""
            if kana_contains(reading, item["correct"]):
                fixed += 1

        # ラベルに無い行への修正（誤検知の可能性もラベル漏れの可能性もあるので、
        # 人間が中身を見て判断できるよう surface/heard/correct を残しておく）
        unlabelled_fixes = [d for d in report["details"]
                            if d["outcome"] == "applied" and d["index"] not in expected_lines]

        usage = reading_check_mod.last_usage
        price = _price_for(model)
        cost = None
        if usage and price:
            cost = usage["input_tokens"] / 1_000_000 * price[0] + usage["output_tokens"] / 1_000_000 * price[1]

        per_model[model] = {
            "fixed": fixed,
            "applied": report["applied"],
            "unverified": report["unverified"],
            "rejected_scope": report["rejected_scope"],
            "unfixable": report["unfixable"],
            "unlabelled_fixes": unlabelled_fixes,
            "usage": usage,
            "cost_usd": cost,
        }
        print(f"[{name}] {model}: 修正{fixed}/{len(baseline_misread)} / "
             f"適用{report['applied']}件 / 検証不能{report['unverified']}件 / "
             f"範囲不審で除外{report['rejected_scope']}件 / 修正できず{report['unfixable']}件 / "
             f"ラベル外の修正{len(unlabelled_fixes)}件")

    return {"name": name, "n_lines": len(lines), "n_expected": len(expected),
           "baseline_misread": len(baseline_misread), "per_model": per_model}


def _build_dict_checks_markdown(dict_results: list[dict[str, Any]]) -> str:
    lines = ["## 読み辞書（assets/reading_dict.yaml）チェック結果", ""]
    n_fail = sum(1 for r in dict_results if not r["passed"])
    lines.append(f"{len(dict_results)}件中 {len(dict_results) - n_fail}件成功 / {n_fail}件失敗")
    lines.append("")
    if n_fail:
        lines.append("### 失敗した項目")
        lines.append("")
        for r in dict_results:
            if r["passed"]:
                continue
            note = f"（{r['note']}）" if r.get("note") else ""
            role = f"[{r['role']}] " if r.get("role") else ""
            if r.get("error"):
                lines.append(f"- {role}{note} 「{r['text']}」-> エラー: {r['error']}")
            else:
                lines.append(f"- {role}{note} 「{r['text']}」-> 「{r['reading']}」"
                             f"（「{r['must_contain']}」を含まず）")
        lines.append("")
    return "\n".join(lines)


def _build_markdown(results: list[dict[str, Any]], models: list[str]) -> str:
    lines = ["## 読み検証 評価結果", "", "### モデル別サマリー（全台本合計）", "",
             "| モデル | ラベル誤読数 | 修正数 | 再現率 | 検証不能 | 範囲不審で除外 | 修正できず | "
             "ラベル外修正 | 入力トークン | 出力トークン | 概算コスト |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for model in models:
        total_expected = sum(r["baseline_misread"] for r in results)
        total_fixed = sum(r["per_model"][model]["fixed"] for r in results)
        total_unverified = sum(r["per_model"][model]["unverified"] for r in results)
        total_rejected_scope = sum(r["per_model"][model]["rejected_scope"] for r in results)
        total_unfixable = sum(r["per_model"][model]["unfixable"] for r in results)
        total_unlabelled = sum(len(r["per_model"][model]["unlabelled_fixes"]) for r in results)
        total_in = sum((r["per_model"][model]["usage"] or {}).get("input_tokens", 0) for r in results)
        total_out = sum((r["per_model"][model]["usage"] or {}).get("output_tokens", 0) for r in results)
        costs = [r["per_model"][model]["cost_usd"] for r in results]
        cost_str = f"${sum(c for c in costs if c is not None):.4f}" if any(c is not None for c in costs) else "?"
        recall_str = f"{total_fixed}/{total_expected}"
        if total_expected:
            recall_str += f"（{total_fixed / total_expected:.0%}）"
        lines.append(f"| {model} | {total_expected} | {total_fixed} | {recall_str} | "
                     f"{total_unverified} | {total_rejected_scope} | {total_unfixable} | "
                     f"{total_unlabelled} | {total_in} | {total_out} | {cost_str} |")
    lines.append("")

    lines.append("### 台本別内訳")
    lines.append("")
    for r in results:
        lines.append(f"#### {r['name']}（{r['n_lines']}行 / ラベル{r['n_expected']}件 / "
                     f"現状誤読{r['baseline_misread']}件）")
        lines.append("")
        for model in models:
            m = r["per_model"][model]
            recall = f"{m['fixed']}/{r['baseline_misread']}"
            if r["baseline_misread"]:
                recall += f"（{m['fixed'] / r['baseline_misread']:.0%}）"
            lines.append(f"- **{model}**: 修正 {recall} / 検証不能 {m['unverified']}件 / "
                         f"範囲不審で除外 {m['rejected_scope']}件 / 修正できず {m['unfixable']}件")
            if m["unlabelled_fixes"]:
                lines.append("  - ラベル外の行への修正（誤検知かラベル漏れか要確認）:")
                for d in m["unlabelled_fixes"]:
                    lines.append(f"    - {d['index']}行目 「{d['surface']}」 {d['heard']}→{d['correct']}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="読み検証パイプラインの再現率とコストを実測する")
    ap.add_argument("--models", help="カンマ区切りのモデル名（既定: config.yamlのscript.reading_check_model"
                                     " / 無ければscript.model）")
    ap.add_argument("--fixtures", default=str(ROOT / "tests" / "fixtures" / "reading_eval"),
                    help="評価用台本(JSON)が入ったディレクトリ")
    ap.add_argument("--dict-only", action="store_true",
                    help="読み辞書(dict_checks.json)のチェックだけ行い、LLMを使う台本評価は"
                         "スキップする（APIキー不要。CI側の固定バージョンエンジンで辞書だけ検証したい時用）")
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))

    fixtures_dir = Path(args.fixtures)
    engine = get_engine(cfg["tts"])
    engine.prepare()  # CIではここでreading_dict.yamlがエンジンのユーザー辞書に同期される

    dict_checks = _load_dict_checks(fixtures_dir)
    dict_report_md = ""
    dict_ok = True
    if dict_checks:
        dict_results = run_dict_checks(engine, dict_checks)
        dict_ok = all(r["passed"] for r in dict_results)
        dict_report_md = _build_dict_checks_markdown(dict_results)
        print(dict_report_md)
    else:
        print("[warn] dict_checks.jsonが無いため読み辞書チェックをスキップ")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path and dict_report_md:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(dict_report_md + "\n")

    if args.dict_only:
        if not dict_ok:
            raise SystemExit("読み辞書チェックに失敗した項目があります（詳細は上記）")
        return

    if args.models:
        models = [m.strip() for m in args.models.split(",") if m.strip()]
    else:
        models = [cfg["script"].get("reading_check_model") or cfg["script"]["model"]]

    fixtures = _load_fixtures(fixtures_dir)
    if not fixtures:
        raise SystemExit(f"評価用の台本が見つからない: {fixtures_dir}")

    results = [run_fixture(engine, name, data, models) for name, data in fixtures]

    report_md = _build_markdown(results, models)
    print("\n" + report_md)

    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(report_md + "\n")


if __name__ == "__main__":
    main()
