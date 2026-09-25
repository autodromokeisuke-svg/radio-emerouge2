# PROGRESS — 読み間違え対策＋システム古さチェック

作業ブランチ: `work/reading-and-deps`（mainへのマージはケイスケ確認後）
中断したらこのファイルの「次にやること」から再開する。

## 前回（中断2回）までの到達点 — 2026-09-25 復元
すべて main 上に未コミットで残っていたため、本ブランチに「前回作業の保存」としてコミットした。
- 読み検証（`src/reading_check.py`）と build_audio への統合、辞書 `assets/reading_dict.yaml` +107行
- 評価ツール `tools/reading_eval.py` ＋ `reading-eval` ワークフロー（手動）＋ fixtures（9/22〜9/24）
- `tests.yml`（CIでpytest）、`requirements-dev.txt`
- daily-radio.yml: ubuntu-24.04固定、actions更新、Secretsを収録ステップだけに限定、
  gh-pages空デプロイ防止、本日分配信済みなら早期スキップ
- requirements.txt: メジャー上限付与 / start_engine.sh: エンジン 1.2.0 固定
- 未検証: ローカルでpytest未実行（pytest未導入）、本番未実行

## フェーズ
- [x] P0 復元・ブランチ作成・前回作業の保存
- [ ] P1 前回変更の受け入れ確認（テスト実行、actionsのバージョンが実在するか）
- [ ] P2 読み間違え事例の抽出（直近2〜3週間の台本）
- [ ] P3 対策方針決定（既知:「英字略語（カタカナ）」の二重読み。現状コードに専用処理なし）
- [ ] P4 実装＋変換結果テスト
- [ ] P5 古さチェック一覧（依存・actions・AivisSpeech・モデル指定）→安全な更新のみ適用
- [ ] P6 最終報告（変更点／未対応／判断事項／実音声確認リスト）

## 要確認（最後にまとめて質問）
- `.codex/` と `AGENTS.md`（Codex用の分担定義。CLAUDE.mdとほぼ同内容）を公開リポジトリに含めてよいか
- 台本生成モデル `claude-sonnet-4-6`（config.yaml）→ 新モデルへの変更は候補として報告のみ（config変更は要承認）
