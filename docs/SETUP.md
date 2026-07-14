# SETUP.md — ケイスケがやること

Claude Codeが自動化できない「人間側の準備」だけをまとめた手順書。
所要時間の目安つき。順番どおりでOK。

## 1. GitHubまわり（約15分・無料）

1. GitHubアカウントを作る → https://github.com （持ってたらスキップ）
2. GitHub CLI（gh）を入れる
   - Windows: `winget install GitHub.cli`
   - Mac: `brew install gh`
3. ログイン: ターミナルで `gh auth login` → 「GitHub.com」「HTTPS」「ブラウザで認証」を選ぶ

※リポジトリは**public（公開）**で作る。無料プランでGitHub Pages配信と
Actions無制限実行ができるのがpublicだから。公開されるのは
「このプログラム」と「AIニュースのラジオ音声」だけで、APIキー等の秘密は
Secretsという金庫に入るので漏れない。

## 2. Anthropic APIキー（約10分・従量課金）

台本生成に使う。**Claude Proの契約とは別物**（別途の従量課金）。

1. https://console.anthropic.com にサインイン
2. Billing でクレジットを購入（まず $5 で十分。20分放送×毎日で月$5前後の消費目安）
3. API Keys → Create Key → キーをコピーして手元に控える
   （あとでClaude Codeに「Secretsに登録して」と言われたら貼る）

## 3. ffmpeg（約5分・無料）

音声の結合に必要。
- Windows: `winget install Gyan.FFmpeg` → ターミナル再起動
- Mac: `brew install ffmpeg`

## 4. Claude Codeでセットアップ開始（約20〜40分）

1. このキットのフォルダをPCの好きな場所に置く
2. そのフォルダでターミナルを開き `claude` を起動
3. 最初にこう伝える:
   **「CLAUDE.mdを読んで、初回セットアップ・ランブックを開始して」**
4. あとはClaude Codeが進める。APIキーの入力を求められたら手順2のキーを貼る
5. 完了すると「RSS URL」が渡される（次の手順で使う）

## 5. 声の聴き比べ（約20分・無料・1回だけ）

日々の放送はクラウドで動くけど、**声選びだけはPCで一度やる**のが早い。

1. AivisSpeech公式アプリを入れる → https://aivis-project.com
2. アプリを一度起動しておく（裏でエンジンが立ち上がる）
3. キットのフォルダで:
   - 話者一覧を見る: `python tools/audition.py --list-speakers`
   - 聴き比べ音源を作る: `python tools/audition.py`
   → `audition_out/aivis.mp3` に、パイロット#0がエメ＆ルジェ声で入る
4. 気に入らなければ AivisHub（https://hub.aivis-project.com）でモデルを
   アプリに追加 → config.yaml の `eme` / `ruje` の speaker/style を書き換えて再実行
5. VOICEVOXも試すなら公式アプリを起動して `python tools/audition.py --engine voicevox`

決めた設定をClaude Codeに伝えれば、次の放送から反映される。

## 6. スマホ（YouTube Music）に番組を追加（約3分）

1. YouTube Musicアプリ →「ライブラリ」→ ポッドキャスト
2. メニューから「RSSフィードで追加」（見つからない時はライブラリ右上のメニュー内）
3. 手順4でもらったURLを貼る: `https://<ユーザー名>.github.io/<リポジトリ名>/feed.xml`
4. 番組を開いて**自動ダウンロードをON**（Wi-Fi設定推奨）
5. 車ではいつも通りBluetooth再生。毎朝4:30すぎに新しい回が降ってくる

うまく追加できない時の代替: ポッドキャスト専用アプリ
（Android: AntennaPod / iPhone: Apple Podcasts の「URLで追加」）でも同じURLが使える。

## 付録A: ElevenLabsに声優交代したくなったら（任意）

1. https://elevenlabs.io でアカウント作成（無料枠で試せる。常用はCreator $22/月目安）
2. Voice Design で下のプロンプトを使い、エメとルジェの声を生成 → 各voice_idを控える
3. Claude Codeに「ElevenLabsに切り替えて。voice_idはこれ」と伝える
   （config変更とSecrets登録をやってくれる）

**エメ用プロンプト例（英語で指定するのがコツ）**
A bright, high-energy young female Japanese speaker in her early twenties.
Cheerful anime-adjacent radio host voice, slightly fast, expressive and playful,
clear articulation. Native Japanese.

**ルジェ用プロンプト例**
A calm, warm young female Japanese speaker in her mid twenties.
Composed and friendly radio co-host, slightly lower pitch, gentle and steady pace,
reassuring tone. Native Japanese.

## 付録B: 番組ジングルを付けたい時（任意）

`assets/jingle.mp3` という名前で3〜6秒の音を置くだけで、
冒頭と末尾に自動で流れる。（自作曲や権利フリー音源を使うこと）
