"""配信サイト(site/)を更新する。

site/
  episodes/radio-YYYYMMDD.mp3   … 放送音源
  episodes/radio-YYYYMMDD.json  … タイトル等のメタ情報
  feed.xml                      … ポッドキャストRSS（YouTube Music等に登録するURL）
  index.html                    … ブラウザ用の簡易アーカイブページ
  cover.jpg                     … 番組カバーアート（assets/cover.jpgがあれば毎回同期）

古いエピソードは episodes_keep 件を超えた分から自動削除。

以前は <itunes:block>Yes</itunes:block> を付けて検索避けをしていたが、
YouTube Music が新エピソードを取り込まない原因の可能性があったため外した。
"""
from __future__ import annotations

import html
import json
import shutil
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any

from mutagen.mp3 import MP3

JST = timezone(timedelta(hours=9))


def build_episode_title(show_title: str, dt: datetime) -> str:
    """放送日から機械的にエピソードタイトルを組み立てる。

    台本生成AIが自由に付けるタイトル文字列は表記が日によってブレる
    （「木曜日」が付いたり「号」が抜けたりする）ため、配信用タイトルは
    このように必ずコード側で固定フォーマットに組み立てる。
    形式: "{show_title} {月}月{日}日号（{月}/{日}）"（ゼロ埋めしない）。
    """
    return f"{show_title} {dt.month}月{dt.day}日号（{dt.month}/{dt.day}）"


_REQUIRED_META_KEYS = ("date", "pub", "title", "description", "file", "bytes")


def _read_duration_sec(mp3_path: Path) -> int | None:
    """MP3ファイルの再生時間（秒・整数）を返す。読めない場合はNone（例外は投げない）。"""
    try:
        return int(round(MP3(mp3_path).info.length))
    except Exception:
        return None


def _episode_meta(site: Path) -> list[dict[str, Any]]:
    metas = []
    for j in sorted((site / "episodes").glob("radio-*.json")):
        try:
            meta = json.loads(j.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(meta, dict) or not all(k in meta for k in _REQUIRED_META_KEYS):
            print(f"[warn] エピソードメタ情報が不正なためスキップ: {j.name}")
            continue
        if "duration_sec" not in meta:
            # 旧エピソード互換: JSONに無ければMP3から補完し、可能なら書き戻す
            duration_sec = _read_duration_sec(site / "episodes" / meta["file"])
            if duration_sec is not None:
                meta["duration_sec"] = duration_sec
                try:
                    j.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
                except OSError:
                    pass
        metas.append(meta)
    return sorted(metas, key=lambda m: m["date"])  # 古い→新しい


_GLOSSARY_KEEP_DAYS = 90
_NEWS_KEEP_DAYS = 30


def load_recent_glossary_terms(site: Path, days: int = 30,
                               since: str = "") -> list[dict[str, str]]:
    """site/glossary_history.json から、直近days日以内に使った用語一覧を返す。

    since（YYYYMMDD）を渡すと、それより前の日付を除外する。非公開の試験運用期間の
    放送内容を台本生成AIに見せると、リスナーが知らない放送へ言及してしまうため
    （詳細は write_script._drop_before_publish のコメント）。
    ファイルが無い/壊れている場合は空リストを返す（例外を投げない）。
    """
    path = site / "glossary_history.json"
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(entries, list):
        return []
    cutoff = datetime.now(JST) - timedelta(days=days)
    result = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        date = e.get("date", "")
        term = e.get("term", "")
        if since and date < since:
            continue
        try:
            dt = datetime.strptime(date, "%Y%m%d").replace(tzinfo=JST)
        except ValueError:
            continue
        if dt >= cutoff:
            result.append({"date": date, "term": term})
    return result


def _load_glossary_by_date(site: Path) -> dict[str, str]:
    """site/glossary_history.json を日数フィルタなしで全件読み、{date: term}の辞書を返す。

    表示用途（番組ページの「今日のひとこと」表示）専用。ファイルが無い/壊れている
    場合は空辞書を返す（例外を投げない）。
    """
    path = site / "glossary_history.json"
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(entries, list):
        return {}
    result = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        date = e.get("date", "")
        term = e.get("term", "")
        if date and term:
            result[date] = term
    return result


def record_glossary_term(site: Path, date_key: str, term: str) -> None:
    """今日使った用語をsite/glossary_history.jsonに記録する。

    term が空文字/Noneなら何もしない。
    同じdate_keyの既存エントリがあれば上書きする（同日再実行時に重複させない）。
    保存後、90日より古いエントリは削除してファイルサイズを抑える。
    """
    if not term:
        return
    path = site / "glossary_history.json"
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            entries = []
    except (OSError, json.JSONDecodeError):
        entries = []

    entries = [e for e in entries if isinstance(e, dict) and e.get("date") != date_key]
    entries.append({"date": date_key, "term": term})

    cutoff = datetime.now(JST) - timedelta(days=_GLOSSARY_KEEP_DAYS)
    kept = []
    for e in entries:
        try:
            dt = datetime.strptime(e.get("date", ""), "%Y%m%d").replace(tzinfo=JST)
        except ValueError:
            continue
        if dt >= cutoff:
            kept.append(e)
    kept.sort(key=lambda e: e["date"])

    path.write_text(json.dumps(kept, ensure_ascii=False, indent=1), encoding="utf-8")


def load_recent_news_titles(site: Path, days: int = 7,
                            since: str = "") -> list[dict[str, str]]:
    """site/news_history.json から、直近days日以内に使ったニュース一覧を返す。

    各要素は {"date": "20260711", "title": str, "link": str} の形。
    since（YYYYMMDD）を渡すと、それより前の日付を除外する。理由は
    load_recent_glossary_terms と同じ。
    ファイルが無い/壊れている場合は空リストを返す（例外を投げない）。
    """
    path = site / "news_history.json"
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(entries, list):
        return []
    cutoff = datetime.now(JST) - timedelta(days=days)
    result = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        date = e.get("date", "")
        title = e.get("title", "")
        link = e.get("link", "")
        if since and date < since:
            continue
        try:
            dt = datetime.strptime(date, "%Y%m%d").replace(tzinfo=JST)
        except ValueError:
            continue
        if dt >= cutoff:
            result.append({"date": date, "title": title, "link": link})
    return result


def record_used_news(site: Path, date_key: str, items: list[dict[str, str]]) -> None:
    """今日使ったニュース一覧をsite/news_history.jsonに記録する。

    items は [{"title": str, "link": str}, ...] のようなリスト
    （dateキーは無くてよい、この関数内でdate_keyを付与する）。
    同じdate_keyの既存エントリがあれば丸ごと上書きする（同日再実行時に重複させない）。
    保存後、30日より古いエントリは削除する。
    """
    path = site / "news_history.json"
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            entries = []
    except (OSError, json.JSONDecodeError):
        entries = []

    entries = [e for e in entries if isinstance(e, dict) and e.get("date") != date_key]
    for it in items:
        entries.append({"date": date_key, "title": it.get("title", ""), "link": it.get("link", "")})

    cutoff = datetime.now(JST) - timedelta(days=_NEWS_KEEP_DAYS)
    kept = []
    for e in entries:
        try:
            dt = datetime.strptime(e.get("date", ""), "%Y%m%d").replace(tzinfo=JST)
        except ValueError:
            continue
        if dt >= cutoff:
            kept.append(e)
    kept.sort(key=lambda e: e["date"])

    path.write_text(json.dumps(kept, ensure_ascii=False, indent=1), encoding="utf-8")


def _filter_publishable(metas: list[dict[str, Any]], publish_from: str) -> list[dict[str, Any]]:
    """publish_from（YYYYMMDD）より前の日付のエピソードを除外する。

    配信フィード/配信ページの表示にのみ使うフィルタ。episodes_keepによる
    自動削除の判定や、用語・ニュースの重複回避履歴には絶対に使わないこと
    （過去分を参照し続ける必要があるため）。

    publish_from が空文字・未設定・不正な日付形式の場合は、全件をそのまま
    返す（fail-soft。例外は投げない）。
    """
    if not publish_from:
        return metas
    try:
        datetime.strptime(publish_from, "%Y%m%d")
    except (ValueError, TypeError):
        print(f"[warn] publish_fromの日付形式が不正なため無視します: {publish_from!r}")
        return metas
    return [m for m in metas if m.get("date", "") >= publish_from]


def update_site(site: Path, mp3_src: Path, title: str, description: str,
                base_url: str, show_cfg: dict[str, Any], glossary_term: str = "") -> None:
    episodes = site / "episodes"
    episodes.mkdir(parents=True, exist_ok=True)

    now = datetime.now(JST)
    date_key = now.strftime("%Y%m%d")
    mp3_name = f"radio-{date_key}.mp3"
    shutil.copy2(mp3_src, episodes / mp3_name)
    new_meta = {
        "date": date_key,
        "pub": now.isoformat(),
        "title": title,
        "description": description,
        "file": mp3_name,
        "bytes": (episodes / mp3_name).stat().st_size,
    }
    duration_sec = _read_duration_sec(episodes / mp3_name)
    if duration_sec is not None:
        new_meta["duration_sec"] = duration_sec
    (episodes / f"radio-{date_key}.json").write_text(
        json.dumps(new_meta, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    record_glossary_term(site, date_key, glossary_term)

    # ---- 古いエピソードの整理 ----
    metas = _episode_meta(site)
    keep = int(show_cfg.get("episodes_keep", 14))
    for old in metas[:-keep] if len(metas) > keep else []:
        for suffix in (".mp3", ".json"):
            p = episodes / old["file"].replace(".mp3", suffix)
            p.unlink(missing_ok=True)
        print(f"[ok] 古い放送を削除: {old['date']}")
    metas = _episode_meta(site)

    # ---- フィード/ページへの表示対象を絞り込み ----
    # ここでの除外は表示のみに影響させる。episodes_keepの削除判定（上のブロック）や
    # 用語・ニュース履歴（load_recent_glossary_terms等）には一切使わないこと。
    publish_metas = _filter_publishable(metas, str(show_cfg.get("publish_from", "")))

    has_cover = _sync_cover(site)
    glossary_by_date = _load_glossary_by_date(site)
    _write_feed(site, publish_metas, base_url, show_cfg, has_cover)
    _write_index(site, publish_metas, show_cfg, has_cover, glossary_by_date)
    (site / ".nojekyll").write_text("", encoding="utf-8")
    print(f"[ok] 配信更新: {len(publish_metas)}エピソード（保存済み{len(metas)}件） / {base_url}/feed.xml")


def _sync_cover(site: Path) -> bool:
    """assets/cover.jpg があれば site/cover.jpg に同期する。"""
    src = Path(__file__).resolve().parent.parent / "assets" / "cover.jpg"
    if not src.exists():
        return False
    shutil.copy2(src, site / "cover.jpg")
    return True


# --------------------------------------------------------------
def _write_feed(site: Path, metas: list[dict], base_url: str,
                show: dict[str, Any], has_cover: bool) -> None:
    e = html.escape
    image_tag = (f'    <itunes:image href="{e(base_url)}/cover.jpg"/>\n'
                 if has_cover else "")
    last_build_date = format_datetime(datetime.now(JST))
    pub_date_tag = ""
    if metas:
        latest_pub = format_datetime(datetime.fromisoformat(metas[-1]["pub"]))
        pub_date_tag = f"    <pubDate>{latest_pub}</pubDate>\n"

    credit = show.get("credit")
    full_description = show["description"]
    if credit:
        full_description = f"{full_description}\n\n{credit}"

    category = show.get("category")
    category_tag = f'    <itunes:category text="{e(category)}"/>\n' if category else ""
    explicit = "true" if show.get("explicit", False) else "false"

    owner_email = show.get("owner_email")
    owner_tag = ""
    if owner_email:
        owner_tag = (
            "    <itunes:owner>\n"
            f"      <itunes:name>{e(show['author'])}</itunes:name>\n"
            f"      <itunes:email>{e(owner_email)}</itunes:email>\n"
            "    </itunes:owner>\n"
        )

    items = []
    for m in reversed(metas):  # 新しい順
        pub = format_datetime(datetime.fromisoformat(m["pub"]))
        url = f"{base_url}/episodes/{m['file']}"
        duration_tag = (f"\n      <itunes:duration>{int(m['duration_sec'])}</itunes:duration>"
                        if "duration_sec" in m else "")
        items.append(f"""    <item>
      <title>{e(m['title'])}</title>
      <description>{e(m['description'])}</description>
      <enclosure url="{e(url)}" length="{m['bytes']}" type="audio/mpeg"/>
      <guid isPermaLink="true">{e(url)}</guid>
      <pubDate>{pub}</pubDate>{duration_tag}
    </item>""")
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>{e(show['title'])}</title>
    <link>{e(base_url)}/</link>
    <language>ja</language>
    <description>{e(full_description)}</description>
    <lastBuildDate>{last_build_date}</lastBuildDate>
{pub_date_tag}    <itunes:author>{e(show['author'])}</itunes:author>
{category_tag}    <itunes:explicit>{explicit}</itunes:explicit>
    <itunes:type>episodic</itunes:type>
{owner_tag}    <itunes:summary>{e(full_description)}</itunes:summary>
{image_tag}{chr(10).join(items)}
  </channel>
</rss>
"""
    (site / "feed.xml").write_text(feed, encoding="utf-8")


# --------------------------------------------------------------
_NEWS_PREFIX = "今日の話題: "
_NEWS_SEP = " / "


def _split_news_titles(description: str | None) -> list[str]:
    """descriptionをニューストピックのタイトル一覧に変換する。

    "今日の話題: A / B / C" 形式なら ["A", "B", "C"] を返す。
    接頭辞が無い場合（ニュース収集に失敗した日のshow.descriptionなど）は
    分割せず、description全体を1件だけのリストとして返す。
    空文字/None/キー無しの場合は空リストを返す（例外は投げない）。
    """
    if not description:
        return []
    text = description.strip()
    if not text:
        return []
    if text.startswith(_NEWS_PREFIX):
        rest = text[len(_NEWS_PREFIX):]
        titles = [t.strip() for t in rest.split(_NEWS_SEP) if t.strip()]
        return titles if titles else [text]
    return [text]


def _news_list_html(description: str | None) -> str:
    """ニューストピックの<ul>断片を返す。表示するものが無ければ空文字。

    箇条書きの記号はCSSのlist-styleで付与し、HTMLのテキストノードには
    タイトルだけを入れる（コピー時に記号が混ざらないようにするため）。
    """
    e = html.escape
    titles = _split_news_titles(description)
    if not titles:
        return ""
    items = "\n".join(f"        <li>{e(t)}</li>" for t in titles)
    return f'      <ul class="news-list">\n{items}\n      </ul>\n'


def _glossary_html(glossary: dict[str, str], date: str) -> str:
    """該当日の「今日のひとこと」用語の<dl>断片を返す。無ければ空文字。

    ラベルと用語を別要素（dt/dd）に分け、コピー時に地続きの一語として
    混ざらないようにする。
    """
    e = html.escape
    term = glossary.get(date, "")
    if not term:
        return ""
    return (f'      <dl class="glossary">\n'
            f'        <dt>今日のひとこと</dt>\n'
            f'        <dd>{e(term)}</dd>\n'
            f'      </dl>\n')


def _write_index(site: Path, metas: list[dict], show: dict[str, Any],
                 has_cover: bool, glossary: dict[str, str] | None = None) -> None:
    e = html.escape
    glossary = glossary or {}
    cover_tag = ('<img class="cover" src="cover.jpg" alt="番組カバー">'
                if has_cover else "")
    latest, *rest = list(reversed(metas)) or [None]
    cards = []
    if latest:
        d = latest["date"]
        news_html = _news_list_html(latest.get("description"))
        glossary_html = _glossary_html(glossary, d)
        cards.append(f"""    <section class="latest">
      <p class="onair"><span class="dot"></span>最新の放送</p>
      <h2>{e(latest['title'])}</h2>
      <p class="date">{d[:4]}.{d[4:6]}.{d[6:]}</p>
{news_html}{glossary_html}      <audio controls preload="none" src="episodes/{e(latest['file'])}"></audio>
    </section>""")
    for m in rest:
        d = m["date"]
        news_html = _news_list_html(m.get("description"))
        glossary_html = _glossary_html(glossary, d)
        cards.append(f"""    <article>
      <p class="date">{d[:4]}.{d[4:6]}.{d[6:]}</p>
      <h3>{e(m['title'])}</h3>
{news_html}{glossary_html}      <audio controls preload="none" src="episodes/{e(m['file'])}"></audio>
    </article>""")

    credit = show.get("credit")
    credit_html = (f'  <p class="credit">{e(credit)}</p>\n' if credit else "")

    page = f"""<!doctype html>
<html lang="ja">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>{e(show['title'])}</title>
<style>
  :root {{ --bg:#1c1713; --ink:#eae0d0; --sub:#a89a86; --gold:#c9a44c; --line:#3a3128; }}
  * {{ box-sizing:border-box; margin:0 }}
  body {{ background:var(--bg); color:var(--ink); font-family:"Hiragino Kaku Gothic ProN","Yu Gothic",sans-serif;
         max-width:640px; margin:0 auto; padding:48px 20px 80px; line-height:1.6 }}
  .cover {{ width:100%; aspect-ratio:1/1; object-fit:cover; border-radius:6px;
           border:1px solid var(--line); margin-bottom:20px; display:block }}
  h1 {{ font-family:"Hiragino Mincho ProN","Yu Mincho",serif; font-weight:600; font-size:1.9rem;
       letter-spacing:.06em; border-bottom:1px solid var(--line); padding-bottom:16px }}
  h1 small {{ display:block; color:var(--sub); font-size:.75rem; letter-spacing:.28em; margin-bottom:6px }}
  .onair {{ color:var(--gold); font-size:.78rem; letter-spacing:.22em; margin-bottom:8px }}
  .dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; background:var(--gold);
         margin-right:8px; animation:blink 2.4s infinite }}
  @keyframes blink {{ 50% {{ opacity:.25 }} }}
  @media (prefers-reduced-motion: reduce) {{ .dot {{ animation:none }} }}
  .latest {{ margin:36px 0 12px; padding:24px; border:1px solid var(--gold); border-radius:4px }}
  .latest h2 {{ font-family:"Hiragino Mincho ProN","Yu Mincho",serif; font-size:1.25rem; margin-bottom:2px }}
  article {{ border-top:1px solid var(--line); padding:20px 0 }}
  h3 {{ font-size:1rem; font-weight:600; margin:2px 0 8px }}
  .date {{ color:var(--sub); font-size:.8rem; letter-spacing:.12em }}
  .news-list {{ list-style:disc; padding-left:1.2em; margin:10px 0 0; color:var(--ink);
               font-size:.88rem; line-height:1.7 }}
  .news-list li {{ margin:2px 0 }}
  .news-list li::marker {{ color:var(--gold) }}
  .glossary {{ margin:12px 0 0; padding:10px 12px; border:1px solid var(--line); border-radius:4px }}
  .glossary dt {{ color:var(--sub); font-size:.72rem; letter-spacing:.14em; margin:0 }}
  .glossary dd {{ color:var(--gold); font-size:1rem; font-weight:600; margin:4px 0 0 }}
  audio {{ width:100%; margin-top:10px }}
  .credit {{ color:var(--sub); font-size:.72rem; letter-spacing:.04em; margin-top:40px;
            padding-top:16px; border-top:1px solid var(--line) }}
</style>
<body>
  {cover_tag}
  <h1><small>MORNING COMMUTE PROGRAM</small>{e(show['title'])}</h1>
{chr(10).join(cards)}
{credit_html}</body>
</html>
"""
    (site / "index.html").write_text(page, encoding="utf-8")
