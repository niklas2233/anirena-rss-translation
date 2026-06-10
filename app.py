"""
AniRena → Torznab proxy server.

Add to Prowlarr as a Torznab indexer:
  URL: http://<host>:5000
  API Key: (leave blank or any value)
"""

import re
import time
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import requests
from urllib.parse import urlencode, quote
from flask import Flask, request, Response

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

ANIRENA_RSS = "https://www.anirena.com/rss?adult=1"
INDEXER_TITLE = "AniRena"
INDEXER_URL = "https://www.anirena.com"

# Prowlarr/Torznab anime category
ANIME_CATEGORY_ID = 5070
ANIME_CATEGORY_NAME = "Anime"

FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

# Simple in-memory cache: (timestamp, parsed_items)
_cache: tuple[float, list[dict]] | None = None
CACHE_TTL = 300  # seconds


def fetch_anirena_items() -> list[dict]:
    global _cache
    now = time.time()
    if _cache and now - _cache[0] < CACHE_TTL:
        return _cache[1]

    log.info("Fetching AniRena RSS …")
    try:
        resp = requests.get(ANIRENA_RSS, headers=FETCH_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        log.error("Failed to fetch AniRena RSS: %s", exc)
        return _cache[1] if _cache else []

    items = parse_rss(resp.content)
    _cache = (now, items)
    return items


def parse_rss(content: bytes) -> list[dict]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        log.error("XML parse error: %s", exc)
        return []

    ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
    channel = root.find("channel")
    if channel is None:
        return []

    results = []
    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        description = (item.findtext("description") or "").strip()

        enclosure = item.find("enclosure")
        torrent_url = ""
        size = 0
        if enclosure is not None:
            torrent_url = enclosure.get("url", "")
            try:
                size = int(enclosure.get("length", 0))
            except ValueError:
                size = 0

        # Parse pubDate to a UTC timestamp for sorting
        pub_ts = 0
        if pub_date:
            try:
                pub_ts = parsedate_to_datetime(pub_date).timestamp()
            except Exception:
                pass

        if not title:
            continue

        results.append(
            {
                "title": title,
                "link": link,
                "torrent_url": torrent_url or link,
                "pub_date": pub_date,
                "pub_ts": pub_ts,
                "size": size,
                "description": description,
                "guid": link,
            }
        )

    return results


def normalize_title(title: str) -> str:
    # Sonarr doesn't recognize AI-upscaled resolution tokens like AI2160p, AI1080p.
    # Strip the AI prefix so they parse as standard resolutions.
    return re.sub(r"\bAI(\d{3,4}p)\b", r"\1", title, flags=re.IGNORECASE)


def matches_query(item: dict, query: str) -> bool:
    if not query:
        return True
    return query.lower() in item["title"].lower()


def proxy_torrent_url(torrent_url: str) -> str:
    """Rewrite an AniRena torrent URL to go through our download proxy."""
    if not torrent_url:
        return torrent_url
    return f"{request.host_url}download?url={quote(torrent_url, safe='')}"


def build_torznab_feed(items: list[dict], query: str = "") -> str:
    filtered = [i for i in items if matches_query(i, query)]

    TORZNAB_NS = "http://torznab.com/schemas/2015/feed"
    ET.register_namespace("torznab", TORZNAB_NS)

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = INDEXER_TITLE
    ET.SubElement(channel, "link").text = INDEXER_URL
    ET.SubElement(channel, "description").text = f"{INDEXER_TITLE} Torznab feed"
    ET.SubElement(
        channel,
        f"{{{TORZNAB_NS}}}response",
        {"offset": "0", "total": str(len(filtered))},
    )

    for item in filtered:
        el = ET.SubElement(channel, "item")
        ET.SubElement(el, "title").text = normalize_title(item["title"])
        ET.SubElement(el, "guid", {"isPermaLink": "true"}).text = item["guid"]
        proxied = proxy_torrent_url(item["torrent_url"])
        ET.SubElement(el, "link").text = proxied
        if item["pub_date"]:
            ET.SubElement(el, "pubDate").text = item["pub_date"]
        if item["size"]:
            ET.SubElement(el, "size").text = str(item["size"])
        if item["description"]:
            ET.SubElement(el, "description").text = item["description"]
        if item["torrent_url"]:
            ET.SubElement(
                el,
                "enclosure",
                {
                    "url": proxied,
                    "length": str(item["size"]),
                    "type": "application/x-bittorrent",
                },
            )
        # Torznab attributes
        ET.SubElement(
            el,
            f"{{{TORZNAB_NS}}}attr",
            {"name": "category", "value": str(ANIME_CATEGORY_ID)},
        )
        if item["size"]:
            ET.SubElement(
                el,
                f"{{{TORZNAB_NS}}}attr",
                {"name": "size", "value": str(item["size"])},
            )

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        rss, encoding="unicode"
    )


def build_caps_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<caps>
  <server version="1.1" title="{title}" strapline="AniRena RSS proxy" url="{url}"/>
  <limits max="100" default="50"/>
  <searching>
    <search available="yes" supportedParams="q"/>
    <tv-search available="yes" supportedParams="q,season,ep"/>
    <movie-search available="no" supportedParams=""/>
    <music-search available="no" supportedParams=""/>
    <audio-search available="no" supportedParams=""/>
    <book-search available="no" supportedParams=""/>
  </searching>
  <categories>
    <category id="5000" name="TV"/>
    <category id="5070" name="Anime">
      <subcat id="5071" name="Anime SD"/>
      <subcat id="5072" name="Anime HD"/>
    </category>
  </categories>
</caps>""".format(
        title=INDEXER_TITLE, url=INDEXER_URL
    )


@app.route("/download", methods=["GET"])
def download():
    """Proxy torrent file downloads from AniRena with browser-like headers."""
    url = request.args.get("url", "")
    if not url or not url.startswith("https://www.anirena.com/"):
        return Response("Invalid URL", status=400)

    log.info("Proxying torrent download: %s", url)
    try:
        resp = requests.get(
            url,
            headers={**FETCH_HEADERS, "Referer": "https://www.anirena.com/"},
            timeout=30,
            stream=True,
        )
        resp.raise_for_status()
    except Exception as exc:
        log.error("Torrent download failed: %s", exc)
        return Response(f"Failed to fetch torrent: {exc}", status=502)

    return Response(
        resp.iter_content(chunk_size=8192),
        content_type=resp.headers.get("Content-Type", "application/x-bittorrent"),
        headers={
            "Content-Disposition": resp.headers.get("Content-Disposition", ""),
            "Content-Length": resp.headers.get("Content-Length", ""),
        },
    )


@app.route("/", methods=["GET"])
@app.route("/api", methods=["GET"])
def api():
    t = request.args.get("t", "").lower()
    q = request.args.get("q", "")

    if t == "caps":
        return Response(build_caps_xml(), content_type="application/xml; charset=utf-8")

    if t in ("search", "tvsearch", "rss", ""):
        items = fetch_anirena_items()
        feed = build_torznab_feed(items, query=q)
        return Response(feed, content_type="application/rss+xml; charset=utf-8")

    # Unknown function
    return Response(
        '<?xml version="1.0"?><error code="202" description="No such function"/>',
        status=400,
        content_type="application/xml",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
