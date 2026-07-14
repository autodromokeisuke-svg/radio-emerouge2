# CLAUDE.md — RADIOえめるーじぇ

エメとルジェが毎朝AIニュースを届ける自分専用ラジオ。GitHub Actionsで全自動生成し、
GitHub PagesのポッドキャストRSSで配信する。ケイスケのPCの電源状態に依存しないこと。

## ★ モデル役割分担ポリシー（コスト最適化・必ず守る）

メインセッション（上位モデル＝Fable級を想定）は **設計・仕様判断・セキュリティとコストの監査・受け入れレビューのみ** を担当し、手を動かす作業は自分でやらずサブエージェントに委任する。

| 担当 | エージェント | モデル | 任せる作業 |
|---|---|---|---|
| 実装 | `implementer` | sonnet | コードの実装・修正・テスト・通常デバッグ（既定の作業馬） |
| 雑務 | `chores` | haiku | ファイル整形・リネーム・ドキュメント微修正・依存追加・ログ確認 |
| 難所 | `heavy-debugger` | opus | implementerが2回失敗した不具合の原因究明のみ（乱用禁止） |

- エージェント定義は `.claude/agents/` にあり、frontmatterの `model` でモデル固定済み
- 判断に迷う作業は「安い方に振ってみて、ダメなら1段上げる」
- メインが直接編集してよいのは、このCLAUDE.md自身と設計メモ程度

## 初回セットアップ・ランブック（この順で進める）

人間側の事前準備は `docs/SETUP.md` 参照。以下はClaude Codeが主導する。

1. **前提確認**: `gh auth status` / `git --version` / `ffmpeg -version` / Python 3.11+。
   足りないものはインストール手順を提示（chores可）
2. **リポジトリ作成**: `gh repo create radio-emerouge --public --source=. --push`
   - **publicにする理由**: GitHub PagesとActions無制限枠が無料プランで使えるため。
     公開されるのはこのコードとニュースラジオ音声のみで、秘密情報はSecretsに置く
3. **Secrets登録**: `gh secret set ANTHROPIC_API_KEY`（キー値はケイスケに入力してもらう。
   チャットログやシェル履歴に残さない）
4. **VERIFY項目の検証**（下記リスト）: implementerに委任し、実機確認→修正
5. **初回放送テスト**: `gh workflow run daily-radio` → `gh run watch` でログ確認。
   失敗したらimplementerがデバッグ（2回失敗でheavy-debuggerに昇格）
6. **Pages有効化**: 初回デプロイ後にgh-pagesブランチが生まれるので
   `gh api repos/{owner}/{repo}/pages -X POST -f "source[branch]=gh-pages" -f "source[path]=/"`
   （既に有効ならスキップ。失敗時はSettings→Pagesから手動設定を案内）
7. **受け入れ確認**（メインが監査）: feed.xml がブラウザで開ける / MP3が再生できる /
   所要時間とActions分数 / APIキーがログに漏れていない
8. ケイスケにRSS URL（`https://<owner>.github.io/<repo>/feed.xml`）を渡し、
   YouTube Musicへの登録（SETUP.md手順5）を案内する

## VERIFYリスト（推測で書いた箇所。初回に必ず実機確認）

- [ ] `scripts/start_engine.sh` のAivisSpeech Engineリリースアセット名の
      `--pattern`（`gh release view --repo Aivis-Project/AivisSpeech-Engine` で確認）
- [ ] 同エンジンの起動フラグ（`./run --help`）とポート10101
- [ ] `src/tts/vv_compat.py` の追加モデルインストールAPI（/aivm_models系）。
      extra_model_urls未使用のうちは放置でよい
- [ ] config.yaml のAnneliスタイル名（「テンション高め」「落ち着き」）が
      実際のスタイル名と一致するか（`python tools/audition.py --list-speakers`）
- [ ] VOICEVOX側デフォルト話者名（使う場合のみ）

## 守り（えめるーじぇ流）

- APIキー・トークンをコード/ログ/コミットに絶対に出さない
- `--dangerously-skip-permissions` は使わない
- config.yaml と assets/prompt_script.md の内容変更は必ずケイスケに確認を取る
  （番組の中身＝看板だから）
- 依存追加やワークフロー変更時は、月間コスト影響（Claude API・Actions分数）を一言添える

## 日常運用コマンド

- 手動で今すぐ1本放送: `gh workflow run daily-radio`
- 尺の変更: config.yaml `show.minutes`
- 声優交代: config.yaml `tts.engine`（aivis / voicevox / elevenlabs）
- ElevenLabs切替時: `gh secret set ELEVENLABS_API_KEY` + voice_id 2つをconfigへ
