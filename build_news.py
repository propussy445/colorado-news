#!/usr/bin/env python3
"""
Colorado Weekly News collector.

Reads RSS feeds from Colorado news outlets (plus a Google News search),
keeps stories from the past 7 days, groups stories that are about the same
thing, ranks them by how many different outlets covered them, and saves the
result to news.json for index.html to display.

Uses only Python's standard library, so there is nothing to install.

Run it:        python build_news.py
Test offline:  python build_news.py --feeds-dir path/to/sample_xml_files
"""

import argparse
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Settings you can tweak
# ---------------------------------------------------------------------------

# Local outlets. Add or remove feeds here. If one stops working, the script
# just skips it and keeps going.
FEEDS = [
    {"name": "The Colorado Sun", "url": "https://coloradosun.com/feed/"},
    {"name": "The Denver Post", "url": "https://www.denverpost.com/feed/"},
    {"name": "Colorado Newsline", "url": "https://coloradonewsline.com/feed/"},
    {"name": "Denverite", "url": "https://denverite.com/feed/"},
    {"name": "FOX31 Denver", "url": "https://kdvr.com/feed/"},
    {"name": "KRDO", "url": "https://krdo.com/feed/"},
    {"name": "Sentinel Colorado", "url": "https://sentinelcolorado.com/feed/"},
]

# Google News search: catches big stories from outlets not listed above.
GOOGLE_NEWS_QUERY = "Colorado when:7d"

DAYS_BACK = 7          # how far back to look
MAX_STORIES = 40       # how many stories end up on the page
SIMILARITY = 0.34      # how alike two headlines must be to count as one story (0-1)

USER_AGENT = "Mozilla/5.0 (compatible; ColoradoWeeklyNews/1.0; personal news digest)"

OUTPUT = Path(__file__).with_name("news.json")

# Words too common to help tell stories apart.
STOPWORDS = set("""
a an and are as at be been but by for from has have he her his how in into is it
its new news of on or our out over says said she that the their them they this to
up was we what when where which who why will with you your after about more than
just can could would should not no yes all its colorado colorado's denver co
""".split())


# ---------------------------------------------------------------------------
# Fetching and parsing
# ---------------------------------------------------------------------------

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def clean_text(raw, limit=240):
    """Strip HTML tags and extra spaces; cut to a readable length."""
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    # Many WordPress feeds end with "The post X appeared first on Y."
    text = re.sub(r"The post .* appeared first on .*$", "", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


def parse_date(value):
    if not value:
        return None
    try:
        d = parsedate_to_datetime(value.strip())
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


IMG_IN_HTML = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.I)


def first_image(item):
    """Look for a photo in the common places feeds put one."""
    for el in item.iter():
        tag = el.tag.split("}")[-1]
        if tag in ("content", "thumbnail") and el.get("url"):
            if el.get("medium", "image") == "image":
                return el.get("url")
        if tag == "enclosure" and (el.get("type") or "").startswith("image"):
            return el.get("url")
    # Many WordPress feeds tuck the photo inside the article HTML instead.
    for el in item.iter():
        tag = el.tag.split("}")[-1]
        if tag in ("encoded", "description") and el.text:
            m = IMG_IN_HTML.search(el.text)
            if m and not m.group(1).startswith("data:"):
                return html.unescape(m.group(1))
    return None


OG_IMAGE = re.compile(
    r"""<meta[^>]+(?:property|name)=["'](?:og:image|twitter:image)(?::src)?["'][^>]*>""", re.I)
CONTENT_ATTR = re.compile(r"""content=["']([^"']+)["']""", re.I)


def find_og_image(page_html):
    """The photo a news site shows when a story is shared on social media."""
    for tag in OG_IMAGE.findall(page_html):
        m = CONTENT_ATTR.search(tag)
        if m and m.group(1).startswith("http"):
            return html.unescape(m.group(1))
    return None


def photo_from_article(url):
    """Open the article itself and pull out its main photo."""
    if "news.google.com" in url:
        return None   # Google's links are redirects, not the article
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            page = resp.read(400_000).decode("utf-8", "ignore")   # photo tag is near the top
        return find_og_image(page)
    except Exception:
        return None


def add_missing_photos(ranked, groups_by_link):
    """For stories without a photo, try each outlet's article page until one has one."""
    for story in ranked:
        if story["image"]:
            continue
        for item in groups_by_link[story["link"]]:
            img = photo_from_article(item["link"])
            if img:
                story["image"] = img
                story["image_source"] = item["source"]
                break


def parse_feed(xml_bytes, default_source, is_google=False):
    root = ET.fromstring(xml_bytes)
    stories = []
    for item in root.iter("item"):
        title = clean_text(item.findtext("title"), limit=300)
        link = (item.findtext("link") or "").strip()
        published = parse_date(item.findtext("pubDate"))
        if not title or not link or not published:
            continue

        source = default_source
        summary = clean_text(item.findtext("description"))
        related = 0

        if is_google:
            # Google titles look like "Headline - Outlet Name"
            src_el = item.find("source")
            if src_el is not None and src_el.text:
                source = src_el.text.strip()
                suffix = " - " + source
                if title.endswith(suffix):
                    title = title[: -len(suffix)]
            # Google's description is a list of related articles; its length
            # is a useful hint for how big the story is.
            related = (item.findtext("description") or "").count("<li")
            summary = ""

        stories.append({
            "title": title,
            "link": link,
            "source": source,
            "published": published,
            "summary": summary,
            "image": first_image(item),
            "related": related,
        })
    return stories


def google_news_url(query):
    q = urllib.parse.quote(query)
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def collect(feeds_dir=None):
    """Return (stories, list_of_feeds_that_worked, list_that_failed)."""
    sources = [dict(f, google=False) for f in FEEDS]
    sources.append({"name": "Google News", "url": google_news_url(GOOGLE_NEWS_QUERY), "google": True})

    all_stories, ok, failed = [], [], []
    for src in sources:
        try:
            if feeds_dir:
                # Offline test mode: read <slug>.xml from a folder instead.
                slug = re.sub(r"[^a-z0-9]+", "-", src["name"].lower()).strip("-")
                path = Path(feeds_dir) / f"{slug}.xml"
                if not path.exists():
                    continue
                data = path.read_bytes()
            else:
                data = fetch(src["url"])
            stories = parse_feed(data, src["name"], is_google=src["google"])
            all_stories.extend(stories)
            ok.append(f'{src["name"]} ({len(stories)})')
        except Exception as e:  # one broken feed shouldn't stop the rest
            failed.append(f'{src["name"]}: {type(e).__name__}')
    return all_stories, ok, failed


# ---------------------------------------------------------------------------
# Grouping and ranking
# ---------------------------------------------------------------------------

def keywords(title):
    words = re.findall(r"[a-z0-9']+", title.lower())
    return {w for w in words if len(w) > 2 and w not in STOPWORDS}


def similar(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def group_stories(stories):
    """Put headlines about the same event into one group."""
    groups = []
    for s in sorted(stories, key=lambda s: s["published"], reverse=True):
        kw = keywords(s["title"])
        best, best_score = None, 0.0
        for g in groups:
            score = max(similar(kw, k) for k in g["kw"])
            if score > best_score:
                best, best_score = g, score
        if best and best_score >= SIMILARITY:
            best["items"].append(s)
            best["kw"].append(kw)
        else:
            groups.append({"items": [s], "kw": [kw]})
    return groups


def rank(groups, now):
    ranked = []
    for g in groups:
        items = g["items"]
        outlets = sorted({i["source"] for i in items})
        newest = max(i["published"] for i in items)
        age_days = (now - newest).total_seconds() / 86400
        score = (
            len(outlets) * 3                              # covered by many outlets
            + min(max(i["related"] for i in items), 8)    # Google's related-coverage hint
            + max(0.0, 2 - age_days * 0.3)                # small bonus for being recent
        )
        # Show the version from a local outlet with a summary if there is one
        # (Google News links are redirects with no summary).
        lead = sorted(items, key=lambda i: (bool(i["summary"]), bool(i["image"]),
                                            i["source"] != "Google News"), reverse=True)[0]
        # If the lead has no photo, borrow one from another outlet's version.
        with_photo = next((i for i in [lead] + items if i["image"]), None)
        ranked.append({
            "title": lead["title"],
            "link": lead["link"],
            "source": lead["source"],
            "published": lead["published"].isoformat(),
            "summary": lead["summary"],
            "image": with_photo["image"] if with_photo else None,
            "image_source": with_photo["source"] if with_photo else None,
            "_items": [lead] + [i for i in items if i is not lead],
            "outlets": outlets,
            "coverage": [{"source": i["source"], "title": i["title"], "link": i["link"]}
                         for i in items if i is not lead][:5],
            "score": round(score, 2),
        })
    ranked.sort(key=lambda r: (r["score"], r["published"]), reverse=True)
    return ranked


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--feeds-dir", help="read sample .xml files from this folder instead of the internet")
    parser.add_argument("--now", help="pretend it's this date (ISO), for testing")
    args = parser.parse_args()

    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(days=DAYS_BACK)

    stories, ok, failed = collect(args.feeds_dir)
    recent = [s for s in stories if cutoff <= s["published"] <= now + timedelta(hours=1)]
    ranked = rank(group_stories(recent), now)[:MAX_STORIES]

    if not args.feeds_dir:   # skip in offline test mode
        add_missing_photos(ranked, {r["link"]: r["_items"] for r in ranked})
    for r in ranked:
        r.pop("_items")
    print(f'{sum(1 for r in ranked if r["image"])} of {len(ranked)} stories have photos')

    print("Feeds read:   " + (", ".join(ok) or "none"))
    if failed:
        print("Feeds failed: " + ", ".join(failed))
    print(f"{len(recent)} stories from the past {DAYS_BACK} days -> top {len(ranked)} kept")

    if not ranked:
        # Keep yesterday's page rather than replacing it with an empty one.
        print("No stories found; leaving news.json unchanged.")
        sys.exit(1 if not ok else 0)

    OUTPUT.write_text(json.dumps({
        "updated": now.isoformat(),
        "days": DAYS_BACK,
        "sources_ok": ok,
        "stories": ranked,
    }, indent=2, ensure_ascii=False))
    print(f"Wrote {OUTPUT.name}")


if __name__ == "__main__":
    main()
