"""配信サイト(site/)を更新する。

site/
  episodes/radio-YYYYMMDD.mp3   … 放送音源
  episodes/radio-YYYYMMDD.json  … タイトル等のメタ情報
  feed.xml                      … ポッドキャストRSS（YouTube Music等に登録するURL）
  index.html                    … ブラウザ用の簡易アーカイブページ
  cover.jpg                     … 番組カバーアート（assets/cover.jpgがあれば毎回同期）

古いエピソードは episodes_keep 件を超えた分から自動削除。
<itunes:block>Yes</itunes:block> を付けて、ポッドキャスト検索に載りにくくしてある（自分専用のため）。
"""
from __future__ import annotations

import html
import json
import shutil
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any

JST = timezone(timedelta(hours=9))


def _episode_meta(site: Path) -> list[dict[str, Any]]:
    metas = []
    for j in sorted((site / "episodes").glob("radio-*.json")):
        try:
            metas.append(json.loads(j.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return sorted(metas, key=lambda m: m["date"])  # 古い→新しい


_GLOSSARY_KEEP_DAYS = 90
_NEWS_KEEP_DAYS = 30


def load_recent_glossary_terms(site: Path, days: int = 30) -> list[dict[str, str]]:
    """site/glossary_history.json から、直近days日以内に使った用語一覧を返す。

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
        try:
            dt = datetime.strptime(date, "%Y%m%d").replace(tzinfo=JST)
        except ValueError:
            continue
        if dt >= cutoff:
            result.append({"date": date, "term": term})
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


def load_recent_news_titles(site: Path, days: int = 7) -> list[dict[str, str]]:
    """site/news_history.json から、直近days日以内に使ったニュース一覧を返す。

    各要素は {"date": "20260711", "title": str, "link": str} の形。
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


def update_site(site: Path, mp3_src: Path, title: str, description: str,
                base_url: str, show_cfg: dict[str, Any], glossary_term: str = "") -> None:
    episodes = site / "episodes"
    episodes.mkdir(parents=True, exist_ok=True)

    now = datetime.now(JST)
    date_key = now.strftime("%Y%m%d")
    mp3_name = f"radio-{date_key}.mp3"
    shutil.copy2(mp3_src, episodes / mp3_name)
    (episodes / f"radio-{date_key}.json").write_text(
        json.dumps({
            "date": date_key,
            "pub": now.isoformat(),
            "title": title,
            "description": description,
            "file": mp3_name,
            "bytes": (episodes / mp3_name).stat().st_size,
        }, ensure_ascii=False, indent=1),
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

    has_cover = _sync_cover(site)
    _write_feed(site, metas, base_url, show_cfg, has_cover)
    _write_index(site, metas, show_cfg, has_cover)
    (site / ".nojekyll").write_text("", encoding="utf-8")
    print(f"[ok] 配信更新: {len(metas)}エピソード / {base_url}/feed.xml")


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
    items = []
    for m in reversed(metas):  # 新しい順
        pub = format_datetime(datetime.fromisoformat(m["pub"]))
        url = f"{base_url}/episodes/{m['file']}"
        items.append(f"""    <item>
      <title>{e(m['title'])}</title>
      <description>{e(m['description'])}</description>
      <enclosure url="{e(url)}" length="{m['bytes']}" type="audio/mpeg"/>
      <guid isPermaLink="true">{e(url)}</guid>
      <pubDate>{pub}</pubDate>
    </item>""")
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>{e(show['title'])}</title>
    <link>{e(base_url)}/</link>
    <language>ja</language>
    <description>{e(show['description'])}</description>
    <itunes:author>{e(show['author'])}</itunes:author>
    <itunes:block>Yes</itunes:block>
{image_tag}{chr(10).join(items)}
  </channel>
</rss>
"""
    (site / "feed.xml").write_text(feed, encoding="utf-8")


# --------------------------------------------------------------
def _write_index(site: Path, metas: list[dict], show: dict[str, Any],
                 has_cover: bool) -> None:
    e = html.escape
    cover_tag = ('<img class="cover" src="cover.jpg" alt="番組カバー">'
                if has_cover else "")
    latest, *rest = list(reversed(metas)) or [None]
    cards = []
    if latest:
        d = latest["date"]
        cards.append(f"""    <section class="latest">
      <p class="onair"><span class="dot"></span>最新の放送</p>
      <h2>{e(latest['title'])}</h2>
      <p class="date">{d[:4]}.{d[4:6]}.{d[6:]}</p>
      <audio controls preload="none" src="episodes/{e(latest['file'])}"></audio>
    </section>""")
    for m in rest:
        d = m["date"]
        cards.append(f"""    <article>
      <p class="date">{d[:4]}.{d[4:6]}.{d[6:]}</p>
      <h3>{e(m['title'])}</h3>
      <audio controls preload="none" src="episodes/{e(m['file'])}"></audio>
    </article>""")
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
  audio {{ width:100%; margin-top:10px }}
</style>
<body>
  {cover_tag}
  <h1><small>MORNING COMMUTE PROGRAM</small>{e(show['title'])}</h1>
{chr(10).join(cards)}
</body>
</html>
"""
    (site / "index.html").write_text(page, encoding="utf-8")
