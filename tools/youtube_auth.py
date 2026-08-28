"""YouTubeアップロード用の認証情報（リフレッシュトークン）を取得するスクリプト。

【事前準備】
  リポジトリ直下に、Google Cloud Consoleで作成したOAuthクライアント
  （デスクトップアプリ型）のJSONを `client_secret.json` として置いておく。
  （.gitignore済みなのでコミットされる心配はない）

【実行方法】
  python tools/youtube_auth.py

  実行するとブラウザが自動で開くので、アップロード先にしたいGoogleアカウントで
  ログインし、YouTube動画のアップロード権限を許可する。

【実行後】
  成功すると token.json に認証情報一式が保存され、さらに以下の3ファイルが
  書き出される（すべて.gitignore済み。絶対にコミットしないこと）:
    - youtube_client_id.txt
    - youtube_client_secret.txt
    - youtube_refresh_token.txt

  画面に表示される案内に従って、これらのファイルの中身をGitHub Secretsへ
  登録する（値そのものはターミナルにもログにも表示しない）:

    gh secret set YOUTUBE_CLIENT_ID < youtube_client_id.txt
    gh secret set YOUTUBE_CLIENT_SECRET < youtube_client_secret.txt
    gh secret set YOUTUBE_REFRESH_TOKEN < youtube_refresh_token.txt

  登録が終わったら、上記のtxtファイル（および必要ならtoken.json）は
  ローカルから削除するか、リポジトリ外の安全な場所へ移動しておくこと。

【注意】
  - スコープは https://www.googleapis.com/auth/youtube.upload のみ（最小権限）
  - access_type="offline" と prompt="consent" を指定しているので、
    毎回リフレッシュトークンが新規発行される（再認証が必要な状態を防ぐため）
  - もし「リフレッシュトークンが取得できなかった」と出た場合は、
    Googleアカウントの「アプリへのアクセス権」設定で本アプリの許可を一度取り消してから
    再実行すること（同意画面を強制的に出し直すため）
"""
from __future__ import annotations

from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

ROOT = Path(__file__).resolve().parent.parent
CLIENT_SECRET_PATH = ROOT / "client_secret.json"
TOKEN_PATH = ROOT / "token.json"
CLIENT_ID_OUT = ROOT / "youtube_client_id.txt"
CLIENT_SECRET_OUT = ROOT / "youtube_client_secret.txt"
REFRESH_TOKEN_OUT = ROOT / "youtube_refresh_token.txt"


def main() -> None:
    if not CLIENT_SECRET_PATH.exists():
        print(f"[ng] {CLIENT_SECRET_PATH} が見つかりません。")
        print("先にGoogle Cloud ConsoleでOAuthクライアント（デスクトップアプリ型）を作成し、"
              "そのJSONをリポジトリ直下に client_secret.json として置いてください。")
        return

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_PATH), scopes=SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    if not creds.refresh_token:
        print("[ng] リフレッシュトークンが取得できませんでした。")
        print("Googleアカウントの『アプリへのアクセス権』設定で本アプリの許可を一度取り消してから、"
              "もう一度 python tools/youtube_auth.py を実行してください。")
        return

    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    CLIENT_ID_OUT.write_text(creds.client_id, encoding="utf-8")
    CLIENT_SECRET_OUT.write_text(creds.client_secret, encoding="utf-8")
    REFRESH_TOKEN_OUT.write_text(creds.refresh_token, encoding="utf-8")

    print("[ok] 認証に成功しました。")
    print(f"token.json に認証情報一式を保存し、以下のファイルを書き出しました"
          "（値はここには表示しません。すべて.gitignore対象です）:")
    print(f"  - {CLIENT_ID_OUT.name}")
    print(f"  - {CLIENT_SECRET_OUT.name}")
    print(f"  - {REFRESH_TOKEN_OUT.name}")
    print()
    print("次に、以下のコマンドを実行してGitHub Secretsへ登録してください:")
    print(f"  gh secret set YOUTUBE_CLIENT_ID < {CLIENT_ID_OUT.name}")
    print(f"  gh secret set YOUTUBE_CLIENT_SECRET < {CLIENT_SECRET_OUT.name}")
    print(f"  gh secret set YOUTUBE_REFRESH_TOKEN < {REFRESH_TOKEN_OUT.name}")
    print()
    print("登録が終わったら、これらのtxtファイルはローカルから削除するか、"
          "リポジトリ外の安全な場所へ移動してください。")


if __name__ == "__main__":
    main()
