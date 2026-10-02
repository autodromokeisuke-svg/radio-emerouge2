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

2026-09-25棚卸し: 本番ログ（9/9〜9/23の15回）と `gh release view` で確認済み。

- [x] `scripts/start_engine.sh` のアセット名パターン → 1.2.0 の
      `AivisSpeech-Engine-Linux-x64-1.2.0.7z.001` に一致。バージョンは `AIVIS_ENGINE_VERSION` で固定済み
- [x] 起動フラグ `--host/--port` とポート10101 → 全実行で「エンジン準備OK ("1.2.0")」
- [x] 追加モデルインストールAPI（/aivm_models/install）→ extra_model_urls の2モデルで稼働中
- [x] 話者・スタイル → Anneliは不使用。現行は 木角空_T2モデル / 水巻咲_T2モデル の「ノーマル」
- [ ] VOICEVOX側デフォルト話者名（使う場合のみ。Dockerタグは cpu-0.25.2 に更新済み・未実行）

## 定期点検（放っておくと止まるもの）

- **publicリポジトリは60日間活動が無いとscheduleが自動停止する**（旧リポジトリ
  radio-emerouge は実際に 2026-09-12 に `disabled_inactivity` になった）。mainへの
  コミットが60日空きそうなら1コミット入れるか、`gh api repos/autodromokeisuke-svg/radio-emerouge2/actions/workflows --jq '.workflows[]|"\(.name) \(.state)"'`
  で状態を確認し、止まっていたら `gh workflow enable daily-radio -R autodromokeisuke-svg/radio-emerouge2`
- `runs-on: ubuntu-24.04` に固定中。ubuntu-latest は 2026-10-19〜11-19 に 26.04 へ移行する。
  移行するなら先に workflow_dispatch で ubuntu-26.04 を試す
- 声エンジンは 1.2.0 に固定。上げる時は `scripts/start_engine.sh` の `AIVIS_ENGINE_VERSION` を変え、
  `reading-eval` ワークフローで読みの変化を確認してから

## 重複回避の方針（2026-10-03）

- 「今日のひとこと」は**公開開始日(show.publish_from=20260901)以降の全期間**で重複させない
  （`glossary_reuse_avoid_days: 0` ＝窓なし。履歴 glossary_history.json も無期限保持）。
  非公開期間(〜8/31)の履歴は重複判定にもプロンプトにも使わない（`since` と `_drop_before_publish` の2層）
- 表記揺れ（英語名・カタカナ・略称・括弧つき）は `assets/glossary_aliases.yaml` の別名グループ＋正規化で吸収。
  新しい用語に英語名などの揺れが出たら、このYAMLに1行（1グループ）追記する（重複判定にのみ使う）
- ニュースは直近14日(`news_reuse_avoid_days`)の窓。台本生成に渡す履歴は「日付: 名称」の一覧のみ（台本全文は渡さない）
- プロンプトは固定部 `assets/prompt_script.md`（system・毎日同一）と可変部 `assets/prompt_daily.md`（user）に分割。
  **固定部に日付・履歴・ニュースなど日ごとに変わる値を混ぜない**（キャッシュ接頭辞が崩れる）
- 大型連休（土日祝＋12/29〜1/3が4日以上）の初日と前日の平日だけ、案内ブロックを可変部へ差し込む（`src/holiday_jp.py`）
- 台本モデルは `claude-sonnet-5` を継続。`claude-sonnet-5-5` は現行コードだと思考が出力上限を消費して失敗した
  (2026-10-03 trial-radio)。再検討するなら `output_config.effort` の指定から

## 守り（えめるーじぇ流）

- APIキー・トークンをコード/ログ/コミットに絶対に出さない
- `--dangerously-skip-permissions` は使わない
- config.yaml と assets/prompt_script.md・assets/prompt_daily.md の内容変更は必ずケイスケに確認を取る
  （番組の中身＝看板だから）
- 依存追加やワークフロー変更時は、月間コスト影響（Claude API・Actions分数）を一言添える

## 日常運用コマンド

- 手動で今すぐ1本放送: `gh workflow run daily-radio`
- 尺の変更: config.yaml `show.minutes`
- 声優交代: config.yaml `tts.engine`（aivis / voicevox / elevenlabs）
- ElevenLabs切替時: `gh secret set ELEVENLABS_API_KEY` + voice_id 2つをconfigへ
- 読み間違いの報告が来たら: 実機で誤読条件を特定 → 文脈に関係なく誤読する語は
  `assets/reading_dict.yaml` へ（巻き込み確認の文も `tests/fixtures/reading_eval/dict_checks.json` へ）、
  文脈依存の誤読は読み検証（`src/reading_check.py`）に任せる。効果の実測は
  `gh workflow run reading-eval -R autodromokeisuke-svg/radio-emerouge2`（Claude APIを少額消費。
  `-f dict_only=true` ならAPI不使用で辞書だけ検証）
- ローカルで `tools/audition.py` 等を動かしても、個人のAivisSpeech辞書は書き換えない
  （辞書同期はCI専用。試す時だけ `RADIO_SYNC_READING_DICT=1`）
