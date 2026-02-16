"""
Polymarket Monitor: US Strikes Iran
Monitors news sources and returns True/False based on whether
credible reports indicate the US has struck Iran.

Resolution criteria (from Polymarket):
- US initiates drone, missile, or air strike on Iranian soil
  OR any official Iranian embassy/consulate
- Must be aerial bombs, drones, or missiles (cruise/ballistic)
- Intercepted missiles do NOT count
- Cyberattacks, ground ops, naval shelling do NOT count
"""

import os
import re
import time
import json
import logging
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import quote

import feedparser
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NEWSDATA_API_KEY = os.getenv("NEWSDATA_API_KEY", "")

# Google News RSS search queries (these get polled for free, unlimited)
RSS_QUERIES = [
    "US strikes Iran",
    "US airstrike Iran",
    "US bombs Iran",
    "US missile strike Iran",
    "US drone strike Iran",
    "Pentagon strikes Iran",
    "American military strike Iran",
    "United States attacks Iran",
]

# NewsData.io search keywords (costs credits, used for confirmation)
NEWSDATA_QUERY = "US strike Iran OR US airstrike Iran OR US bombs Iran"

# Phrases that strongly indicate a real strike happened
POSITIVE_SIGNALS = [
    r"us\s+(strikes?|struck|attacks?|attacked|bombs?|bombed)\s+iran",
    r"(american|pentagon|us military)\s+(strikes?|struck|launched?|attacks?)\s+(on\s+)?iran",
    r"(airstrikes?|air\s+strikes?|missile\s+strikes?|drone\s+strikes?)\s+(on|against|hit|target)\s+iran",
    r"iran\s+(hit|struck|bombed|attacked)\s+by\s+(us|united states|american)",
    r"us\s+(launches?|launched)\s+(missiles?|drones?|airstrikes?)\s+(at|on|against)\s+iran",
    r"(cruise|ballistic)\s+missiles?\s+(hit|struck|target|launched).{0,30}iran",
]

# Phrases that indicate this is NOT the event we're looking for
NEGATIVE_SIGNALS = [
    r"cyber\s*(attack|war|strike|operation)",
    r"intercept(ed|ion|s)",
    r"shot\s+down",
    r"sanctions?",
    r"ground\s+(troops?|forces?|invasion|incursion|operativ)",
    r"naval\s+(strike|shell|bombard)",
    r"artillery",
    r"could\s+strike",
    r"may\s+strike",
    r"might\s+strike",
    r"threatens?\s+to\s+strike",
    r"warns?\s+(of|about)\s+(strike|attack)",
    r"if\s+(us|the\s+us|america)\s+strikes?",
    r"plans?\s+to\s+strike",
    r"scenario",
    r"what\s+if",
    r"simulation",
    r"wargame",
    r"iran\s+(strikes?|attacks?|bombs?)\s+(us|united states|america|israel)",
    r"considering\b",
    r"weighing\b",
    r"preparing\s+to",
    r"ready\s+to\s+strike",
    r"will\s+(the\s+)?(us|america)\s+strike",
    r"betting\s+odds",
    r"polymarket",
    r"prediction\s+market",
    r"odds\s+of",
    r"probability",
    r"should\s+(the\s+)?(us|america)\s+strike",
    r"opinion\b",
    r"editorial\b",
    r"analysis\b",
]

# Sources to completely ignore (self-referencing noise)
IGNORED_SOURCES = {"polymarket", "predictit", "kalshi", "metaculus"}

# Only consider articles published within this window
MAX_ARTICLE_AGE_HOURS = 24

# Sources we consider highly credible for military reporting
CREDIBLE_SOURCES = {
    "reuters", "associated press", "ap news", "bbc", "cnn", "al jazeera",
    "new york times", "washington post", "wall street journal", "the guardian",
    "bloomberg", "nbc news", "abc news", "cbs news", "fox news", "sky news",
    "afp", "dw news", "france 24", "times of israel", "breaking defense",
    "defense one", "military times", "pentagon",
}

# ---------------------------------------------------------------------------
# RSS Layer (free, unlimited)
# ---------------------------------------------------------------------------

def parse_pub_date(date_str: str) -> Optional[datetime]:
    """Try to parse an RSS publication date into a timezone-aware datetime."""
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        return None


def is_recent(article: dict) -> bool:
    """Return True if the article was published within MAX_ARTICLE_AGE_HOURS."""
    dt = parse_pub_date(article.get("published", ""))
    if dt is None:
        return True  # If we can't parse the date, include it (fail open)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_ARTICLE_AGE_HOURS)
    return dt >= cutoff


def fetch_google_news_rss(query: str) -> list[dict]:
    """Fetch articles from Google News RSS for a search query."""
    url = f"https://news.google.com/rss/search?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"
    feed = feedparser.parse(url)
    articles = []
    for entry in feed.entries:
        source = entry.get("source", {}).get("title", "").lower() if hasattr(entry, "source") else ""
        if any(ign in source for ign in IGNORED_SOURCES):
            continue
        articles.append({
            "title": entry.get("title", ""),
            "link": entry.get("link", ""),
            "published": entry.get("published", ""),
            "source": source,
        })
    return articles


def poll_rss() -> list[dict]:
    """Poll all RSS queries and deduplicate results."""
    seen_links = set()
    all_articles = []
    for query in RSS_QUERIES:
        articles = fetch_google_news_rss(query)
        for a in articles:
            if a["link"] not in seen_links:
                seen_links.add(a["link"])
                all_articles.append(a)
    log.info(f"RSS returned {len(all_articles)} unique articles")
    return all_articles


# ---------------------------------------------------------------------------
# NewsData.io Layer (200 credits/day, used for confirmation)
# ---------------------------------------------------------------------------

def fetch_newsdata(query: str) -> list[dict]:
    """Search NewsData.io API for articles matching the query."""
    if not NEWSDATA_API_KEY:
        log.warning("No NEWSDATA_API_KEY set — skipping NewsData.io search")
        return []

    url = "https://newsdata.io/api/1/latest"
    params = {
        "apikey": NEWSDATA_API_KEY,
        "q": query,
        "language": "en",
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        articles = []
        for item in data.get("results", []):
            articles.append({
                "title": item.get("title", ""),
                "description": item.get("description", ""),
                "link": item.get("link", ""),
                "source": (item.get("source_id") or "").lower(),
                "published": item.get("pubDate", ""),
            })
        log.info(f"NewsData.io returned {len(articles)} articles")
        return articles
    except requests.RequestException as e:
        log.error(f"NewsData.io request failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Scoring Engine
# ---------------------------------------------------------------------------

def score_article(article: dict) -> dict:
    """
    Score an article for relevance to the market resolution criteria.

    Returns a dict with:
      - positive_score: how many positive signal patterns matched
      - negative_score: how many negative signal patterns matched
      - is_credible: whether the source is in our credible list
      - net_score: final score (positive - negative, boosted by credibility)
    """
    text = f"{article.get('title', '')} {article.get('description', '')}".lower()

    positive_hits = sum(1 for p in POSITIVE_SIGNALS if re.search(p, text))
    negative_hits = sum(1 for p in NEGATIVE_SIGNALS if re.search(p, text))

    source = article.get("source", "").lower()
    is_credible = any(cs in source for cs in CREDIBLE_SOURCES)

    # Credible source gets a 2x multiplier on positive signals
    credibility_boost = 2.0 if is_credible else 1.0
    net_score = (positive_hits * credibility_boost) - (negative_hits * 1.5)

    return {
        "positive_score": positive_hits,
        "negative_score": negative_hits,
        "is_credible": is_credible,
        "net_score": net_score,
    }


# ---------------------------------------------------------------------------
# Decision Logic — YOUR INPUT NEEDED (see below)
# ---------------------------------------------------------------------------

def evaluate(scored_articles: list[dict]) -> dict:
    """
    Aggressive evaluation: triggers fast on early signals.
    Prioritizes speed over certainty — one credible hit or a few
    non-credible hits is enough to flag True.
    """
    hits = [a for a in scored_articles if a["net_score"] > 0]
    credible_hits = [a for a in hits if a["is_credible"]]
    top = hits[:5]

    # Tier 1: Even a single credible source with a strong signal
    if any(a["net_score"] >= 3 and a["is_credible"] for a in hits):
        return {
            "result": True,
            "confidence": 0.9,
            "reason": f"Strong signal from credible source ({credible_hits[0].get('source', 'unknown')})",
            "top_articles": top,
        }

    # Tier 2: Multiple credible sources, even with weaker signals
    if len(credible_hits) >= 2:
        return {
            "result": True,
            "confidence": 0.8,
            "reason": f"{len(credible_hits)} credible sources reporting",
            "top_articles": top,
        }

    # Tier 3: One credible source with any positive signal
    if len(credible_hits) >= 1:
        return {
            "result": True,
            "confidence": 0.6,
            "reason": f"Early signal from {credible_hits[0].get('source', 'unknown')}",
            "top_articles": top,
        }

    # Tier 4: Multiple non-credible sources (could be breaking faster)
    if len(hits) >= 3:
        return {
            "result": True,
            "confidence": 0.4,
            "reason": f"{len(hits)} sources reporting (none yet credible — verify manually)",
            "top_articles": top,
        }

    # No meaningful signal
    return {
        "result": False,
        "confidence": 0.0,
        "reason": f"No credible signals ({len(hits)} weak hits)" if hits else "No relevant articles found",
        "top_articles": top,
    }


# ---------------------------------------------------------------------------
# File Output
# ---------------------------------------------------------------------------

LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


def save_result(result: dict):
    """Save the full result (including all articles) to a timestamped JSON file."""
    os.makedirs(LOGS_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"scan_{timestamp}.json"
    filepath = os.path.join(LOGS_DIR, filename)

    output = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "result": result["result"],
        "confidence": result["confidence"],
        "reason": result["reason"],
        "total_scanned": result.get("total_scanned", 0),
        "total_recent": result.get("total_recent", 0),
        "total_with_signal": len([a for a in result.get("all_articles", []) if a["net_score"] > 0]),
        "articles": result.get("all_articles", []),
    }

    with open(filepath, "w") as f:
        json.dump(output, f, indent=2, default=str)

    log.info(f"Results saved to {filepath}")
    return filepath


# ---------------------------------------------------------------------------
# Main Monitor Loop
# ---------------------------------------------------------------------------

def check_once(use_newsdata: bool = True) -> dict:
    """Run one check cycle: poll RSS, optionally confirm with NewsData.io."""

    # Layer 1: Free RSS polling
    rss_articles = poll_rss()

    # Filter to recent articles only
    recent_articles = [a for a in rss_articles if is_recent(a)]
    log.info(f"Filtered to {len(recent_articles)} articles from last {MAX_ARTICLE_AGE_HOURS}h")

    # Score all recent articles
    scored = []
    for article in recent_articles:
        score = score_article(article)
        scored.append({**article, **score})

    # Sort by net score descending
    scored.sort(key=lambda x: x["net_score"], reverse=True)

    # Layer 2: If RSS found promising signals, confirm with NewsData.io
    top_rss_score = scored[0]["net_score"] if scored else 0
    if use_newsdata and top_rss_score > 0:
        log.info("RSS found positive signals — confirming with NewsData.io")
        newsdata_articles = fetch_newsdata(NEWSDATA_QUERY)
        for article in newsdata_articles:
            score = score_article(article)
            scored.append({**article, **score})
        scored.sort(key=lambda x: x["net_score"], reverse=True)

    result = evaluate(scored)
    result["all_articles"] = scored
    result["total_scanned"] = len(rss_articles)
    result["total_recent"] = len(recent_articles)
    return result


def monitor(interval_seconds: int = 300, use_newsdata: bool = True):
    """Continuously monitor news, checking every `interval_seconds`."""
    log.info(f"Starting monitor — checking every {interval_seconds}s")
    while True:
        try:
            result = check_once(use_newsdata=use_newsdata)
            save_result(result)
            timestamp = datetime.now(timezone.utc).isoformat()
            summary = {k: v for k, v in result.items() if k not in ("all_articles",)}
            print(json.dumps({"timestamp": timestamp, **summary}, indent=2))

            if result["result"]:
                log.warning("*** ALERT: US STRIKE ON IRAN DETECTED ***")
                log.warning(f"Confidence: {result['confidence']:.0%}")
                log.warning(f"Reason: {result['reason']}")
                break  # Stop monitoring once detected
        except NotImplementedError:
            log.error("evaluate() not implemented yet — see monitor.py")
            break
        except Exception as e:
            log.error(f"Check failed: {e}")

        time.sleep(interval_seconds)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Monitor: US Strikes Iran")
    parser.add_argument("--once", action="store_true", help="Run a single check and exit")
    parser.add_argument("--interval", type=int, default=300, help="Seconds between checks (default: 300)")
    parser.add_argument("--no-newsdata", action="store_true", help="Skip NewsData.io (RSS only)")
    args = parser.parse_args()

    if args.once:
        result = check_once(use_newsdata=not args.no_newsdata)
        filepath = save_result(result)
        summary = {k: v for k, v in result.items() if k not in ("all_articles",)}
        print(json.dumps(summary, indent=2))
        print(f"\nFull results with all articles saved to: {filepath}")
    else:
        monitor(interval_seconds=args.interval, use_newsdata=not args.no_newsdata)
