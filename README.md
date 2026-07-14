# RADIOえめるーじぇ 📻

エメとルジェが毎朝AIニュースを届ける、自分専用の全自動ラジオ局。
PCの電源は不要。クラウド（GitHub Actions）が毎朝4:30に収録して、
スマホのYouTube Music（ポッドキャストRSS）に届ける。

```
毎朝4:30 GitHub Actionsが起動（クラウド・PC不要）
   │
   ├─ 1. RSSから直近のAIニュースを収集        (src/collect_news.py)
   ├─ 2. Claude APIがエメ×ルジェの台本を生成   (src/write_script.py)
   ├─ 3. 声エンジンがセリフごとに音声化→1本のMP3 (src/build_audio.py)
   └─ 4. ポッドキャストRSSを更新してPagesへ配信  (src/make_feed.py)
        │
        └→ スマホが自動DL → 車でBluetooth再生 🚗
```

## はじめかた

1. 人間側の準備: **docs/SETUP.md**（GitHubアカウント・APIキー・聴き比べ）
2. Claude Codeでこのフォルダを開いて一言:
   **「CLAUDE.mdを読んで、初回セットアップ・ランブックを開始して」**

## 日常の操作（ぜんぶ config.yaml）

| やりたいこと | 変える場所 |
|---|---|
| 放送時間を変える（20分→30分） | `show.minutes` |
| 声優交代（無料⇔ElevenLabs） | `tts.engine` を1行変更 |
| エメ/ルジェの声・スタイル変更 | `tts.<engine>.eme / ruje` |
| ニュース源の追加・削除 | `news_feeds` |
| 今すぐ1本テスト放送 | `gh workflow run daily-radio` |

## コスト目安（20分/日・毎日放送）

| 項目 | 目安 |
|---|---|
| 声（AivisSpeech / VOICEVOX） | **0円** |
| 台本（Claude API, Sonnet） | 月 数百円〜1,500円程度 |
| GitHub Actions / Pages | 0円（publicリポジトリ） |
| （任意）ElevenLabsに交代時 | +$22/月〜 |

## 構成

```
radio-emerouge/
├── CLAUDE.md                 # Claude Code向け指示（役割分担・ランブック・VERIFY）
├── config.yaml               # ★すべての設定はここ
├── .github/workflows/
│   └── daily-radio.yml       # 毎朝の自動放送
├── .claude/agents/           # サブエージェント（haiku/sonnet/opusの分業）
├── src/
│   ├── run_daily.py          # 全工程のオーケストレーター
│   ├── collect_news.py       # ニュース収集
│   ├── write_script.py       # 台本生成（Claude API）
│   ├── build_audio.py        # 収録（合成→結合→MP3）
│   ├── make_feed.py          # RSS＋アーカイブページ生成
│   └── tts/                  # 声エンジン差し替え層（aivis/voicevox/elevenlabs）
├── scripts/start_engine.sh   # 声エンジンの取得・起動（CI/ローカル共用）
├── tools/audition.py         # 声の聴き比べツール（PCで1回だけ使う）
├── assets/
│   ├── prompt_script.md      # 台本プロンプト（番組の魂。変更は要ケイスケ確認）
│   └── pilot_script_00.md    # パイロット台本#0（聴き比べ・口調基準）
└── docs/SETUP.md             # 人間側の準備手順
```

## メモ

- 配信ページには直近14回分だけ残し、古い放送は自動削除（リポジトリ肥大防止）
- RSSには itunes:block を付けてあり、ポッドキャスト検索には載りにくい自分専用仕様
- ニュース内容は自分専用のため、えめるーじぇの販売品質ルール（一次ソース裏取り等）の対象外
