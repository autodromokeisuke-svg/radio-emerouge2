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
- [x] P1 受け入れ確認: pytest 161合格/2失敗（reading_check周りのロジック不整合。環境要因ではない）→ P4で修正
- [x] P2 事例抽出: **台本本文は gh-pages・artifact・Actionsログのどこにも保存されていない**。
      残っているのは tests/fixtures/reading_eval の9/22〜9/24のみ。その3日分に「略語（カナ）」形式は0件
      （プロンプトに表記ルールが無く、LLMがたまに書く時だけ発生すると判断）
- [x] P3 方針決定:
      1) 主軸＝TTS直前の正規化 `src/reading_normalize.py`（音声用テキストのみ。台本・説明文・Xは不変）
         R1「ラテン（カナ）」→カナのみ（直前が漢字/カタカナならカッコだけ削除）／R2「カナ（ラテン）」→カッコ削除
         ルールはモジュール先頭のリストに1行追記で増やせる
      2) 文脈に依らない誤読語＝既存 `assets/reading_dict.yaml` に追記（運用継続）
      3) プロンプトへの表記ルール追加＝看板変更のため提案のみ（要確認）
      数字・単位・日付の正規化は、誤読の実例が無いので今回は入れない（誤変換リスクの方が大きい）
- [x] P4 実装＋テスト（pytest 183合格）
      - **真因**: 本番にも既存の `_strip_alpha_reading_gloss` があったが、正規表現が単語1つ・ピリオド無しだけ対応。
        「GPT-5.5（…）」「Gemini 3（…）」「LLM （…）」は素通り、「Claude Code（…）」は「Claude クロードコード」と
        二重読みのまま残っていた → `src/reading_normalize.py` に一本化（古い関数は削除）
      - 失敗していたテスト2件は reading_check まわりのコード側の不備を修正（テストは変更なし）
      - 音声用テキストのみに適用（RSS・説明文・Xには流れないことをgrepで確認済み）
- [x] P5 古さ一覧: actions（checkout v7.0.1 / setup-python v7.0.0 / cache v6.1.0 / gh-pages v4）は最新メジャーで実在確認済。
      Python依存・AivisSpeech 1.2.0 も最新。**古いのは台本モデル `claude-sonnet-4-6` のみ**（報告のみ）
- [ ] P6 最終報告（変更点／未対応／判断事項／実音声確認リスト）

## 要確認（最後にまとめて質問）
- `.codex/` と `AGENTS.md`（Codex用の分担定義。CLAUDE.mdとほぼ同内容）を公開リポジトリに含めてよいか
- 台本生成モデル `claude-sonnet-4-6`（config.yaml）→ 新モデルへの変更は候補として報告のみ（config変更は要承認）
