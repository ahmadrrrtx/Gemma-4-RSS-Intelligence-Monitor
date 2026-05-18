#!/usr/bin/env python3
"""
Gemma 4 RSS Intelligence Monitor
=================================
Uses Gemma 4 E4B (Edge 4B) to monitor developer RSS feeds,
filter noise, and post intelligent digests to Slack.

Model choice: gemma4:e4b
- Runs on CPU with 4GB RAM
- 128K context window handles large feed batches
- Built-in reasoning for spam vs signal classification
- Apache 2.0 license — use commercially, no restrictions

Author: Your Name
Challenge: DEV.to Gemma 4 Challenge 2026
"""

import feedparser
import requests
import yaml
import os
import sys
import time
import logging
import argparse
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dateutil import parser as date_parser

# ─────────────────────────────────────────────
# Logging Setup
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("monitor.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Ollama Client (no extra SDK needed)
# ─────────────────────────────────────────────
def call_gemma4(prompt: str, model: str, host: str, temperature: float = 0.2) -> str:
    """
    Call Gemma 4 via Ollama HTTP API.
    Using E4B model: 4B edge model, 128K context, multimodal capable.
    Temperature 0.2 keeps output deterministic for classification tasks.
    """
    url = f"{host}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "top_p": 0.9,
            "num_ctx": 8192,       # More than enough for feed batches
        },
    }
    try:
        resp = requests.post(url, json=payload, timeout=120)
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except requests.exceptions.Timeout:
        log.error("Gemma 4 inference timed out (120s). Try reducing batch size.")
        return "ERROR: Inference timeout"
    except Exception as e:
        log.error(f"Gemma 4 API error: {e}")
        return f"ERROR: {e}"


def check_ollama(host: str, model: str) -> bool:
    """Verify Ollama is running and the Gemma 4 model is available."""
    try:
        resp = requests.get(f"{host}/api/tags", timeout=5)
        resp.raise_for_status()
        models = [m["name"] for m in resp.json().get("models", [])]
        available = any(model.split(":")[0] in m for m in models)
        if not available:
            log.error(f"Model '{model}' not found. Run: ollama pull {model}")
            log.info(f"Available models: {models}")
        return available
    except Exception as e:
        log.error(f"Cannot reach Ollama at {host}: {e}")
        log.info("Start Ollama with: ollama serve")
        return False


# ─────────────────────────────────────────────
# Feed Fetching
# ─────────────────────────────────────────────
def fetch_feed(url: str, name: str, hours_back: int, max_items: int) -> List[Dict]:
    """Fetch recent items from a single RSS/Atom feed."""
    try:
        feed = feedparser.parse(url)
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)
        items = []

        for entry in feed.entries[:max_items]:
            # Parse date robustly
            pub = None
            for attr in ("published_parsed", "updated_parsed"):
                parsed = getattr(entry, attr, None)
                if parsed:
                    pub = datetime(*parsed[:6])
                    break
            if pub is None:
                for attr in ("published", "updated"):
                    raw = getattr(entry, attr, None)
                    if raw:
                        try:
                            pub = date_parser.parse(raw).replace(tzinfo=None)
                        except Exception:
                            pass
                        break

            # Include if recent or undated (fail open — Gemma will filter)
            if pub is None or pub > cutoff:
                items.append(
                    {
                        "feed": name,
                        "title": getattr(entry, "title", "Untitled"),
                        "link": getattr(entry, "link", ""),
                        "summary": (
                            getattr(entry, "summary", getattr(entry, "description", ""))
                            or ""
                        )[:400],
                        "published": pub.strftime("%Y-%m-%d %H:%M") if pub else "Unknown",
                    }
                )

        log.info(f"  [{name}] {len(items)} recent items")
        return items

    except Exception as e:
        log.warning(f"  [{name}] Feed error: {e}")
        return []


def fetch_all_feeds(feeds: List[Dict], hours_back: int, max_items: int) -> List[Dict]:
    """Fetch from all configured feeds."""
    all_items = []
    for feed_cfg in feeds:
        items = fetch_feed(
            url=feed_cfg["url"],
            name=feed_cfg["name"],
            hours_back=hours_back,
            max_items=max_items,
        )
        all_items.extend(items)
    log.info(f"Total items fetched: {len(all_items)}")
    return all_items


# ─────────────────────────────────────────────
# Gemma 4 Analysis
# ─────────────────────────────────────────────
def build_analysis_prompt(items: List[Dict]) -> str:
    """
    Craft a prompt that uses Gemma 4's reasoning capability.
    E4B has configurable thinking mode — low temperature pushes it
    toward structured, reliable classification output.
    """
    items_block = "\n\n".join(
        f"[{i['feed']}] | {i['published']}\n"
        f"TITLE: {i['title']}\n"
        f"SUMMARY: {i['summary']}\n"
        f"LINK: {i['link']}"
        for i in items
    )

    return f"""You are a senior developer curating a technical news digest.

Your task: Review these RSS feed items and identify ONLY items that developers need to know about.

INCLUDE:
- Stable releases of major projects (v1.0+, security patches, major versions)
- Breaking changes or deprecations in popular frameworks
- Critical security vulnerabilities (CVE, patches)
- Significant new capabilities or architectural changes
- Major ecosystem announcements

EXCLUDE:
- Tutorials, how-to guides, "getting started" content
- Promotional blog posts, sponsored content
- Minor patch releases (unless security-related)
- Opinion pieces without new technical information
- Duplicate news from the same event

ITEMS TO REVIEW:
{items_block}

OUTPUT FORMAT (strictly follow this):
If important items found, respond with:

## Developer Digest — {datetime.now().strftime("%B %d, %Y")}

• **[ProjectName vX.Y]** — One sentence on what changed and why it matters. → URL

• **[SECURITY: ProjectName]** — One sentence on the vulnerability/patch. → URL

(Continue for each important item)

---
*{len(items)} items reviewed. X important updates identified.*

If NOTHING is newsworthy: respond with exactly: NO_UPDATES_THIS_CYCLE"""


def analyze_with_gemma4(items: List[Dict], model: str, host: str) -> str:
    """Send items to Gemma 4 E4B for intelligent analysis."""
    if not items:
        return "NO_UPDATES_THIS_CYCLE"

    log.info(f"Sending {len(items)} items to Gemma 4 ({model}) for analysis...")
    t0 = time.time()

    prompt = build_analysis_prompt(items)
    result = call_gemma4(prompt=prompt, model=model, host=host, temperature=0.2)

    elapsed = time.time() - t0
    log.info(f"Gemma 4 analysis completed in {elapsed:.1f}s")

    return result


# ─────────────────────────────────────────────
# Notifications
# ─────────────────────────────────────────────
def post_to_slack(digest: str, webhook_url: str, channel: str, username: str) -> bool:
    """Post digest to Slack via webhook."""
    if not webhook_url or webhook_url == "YOUR_SLACK_WEBHOOK_HERE":
        log.info("Slack not configured. Printing digest:\n")
        print("\n" + "=" * 72)
        print(digest)
        print("=" * 72 + "\n")
        return True

    try:
        payload = {
            "channel": channel,
            "username": username,
            "text": digest,
            "icon_emoji": ":robot_face:",
            "mrkdwn": True,
        }
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code == 200:
            log.info("✓ Digest posted to Slack")
            return True
        else:
            log.error(f"Slack error {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        log.error(f"Slack post failed: {e}")
        return False


# ─────────────────────────────────────────────
# Main Runner
# ─────────────────────────────────────────────
class FeedMonitor:
    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)

        self.model = self.cfg["gemma4"]["model"]
        self.host = self.cfg["gemma4"]["host"]
        self.hours_back = self.cfg["monitoring"]["hours_back"]
        self.max_items = self.cfg["monitoring"]["max_items_per_feed"]
        self.feeds = self.cfg["feeds"]
        self.slack = self.cfg.get("slack", {})

    def run(self):
        log.info("=" * 60)
        log.info(f"Gemma 4 RSS Intelligence Monitor")
        log.info(f"Model: {self.model} | Looking back: {self.hours_back}h")
        log.info(f"Monitoring {len(self.feeds)} feeds")
        log.info("=" * 60)

        # Verify Ollama is up
        if not check_ollama(self.host, self.model):
            sys.exit(1)

        # Fetch feeds
        items = fetch_all_feeds(self.feeds, self.hours_back, self.max_items)

        if not items:
            log.info("No new items found. Nothing to analyze.")
            return

        # Analyze with Gemma 4
        digest = analyze_with_gemma4(items, self.model, self.host)

        if digest == "NO_UPDATES_THIS_CYCLE":
            log.info("Gemma 4: No newsworthy items this cycle.")
            return

        # Notify
        post_to_slack(
            digest=digest,
            webhook_url=self.slack.get("webhook_url", ""),
            channel=self.slack.get("channel", "#dev-digest"),
            username=self.slack.get("username", "Gemma4 Monitor"),
        )

        log.info("Cycle complete.")


def main():
    parser = argparse.ArgumentParser(
        description="Gemma 4 RSS Intelligence Monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python monitor.py                        # Run with default config
  python monitor.py --config my.yaml       # Custom config
  python monitor.py --hours 24             # Override lookback window
  python monitor.py --dry-run              # Fetch + analyze, skip Slack
        """,
    )
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--hours", type=int, help="Override lookback hours")
    parser.add_argument(
        "--dry-run", action="store_true", help="Analyze but do not post to Slack"
    )
    parser.add_argument(
        "--check", action="store_true", help="Check Ollama + config, then exit"
    )
    args = parser.parse_args()

    monitor = FeedMonitor(config_path=args.config)

    if args.hours:
        monitor.hours_back = args.hours
        log.info(f"Overriding lookback to {args.hours} hours")

    if args.dry_run:
        monitor.slack["webhook_url"] = ""
        log.info("Dry run mode — Slack posting disabled")

    if args.check:
        ok = check_ollama(monitor.host, monitor.model)
        if ok:
            log.info(f"✓ Config OK | Model: {monitor.model} | Feeds: {len(monitor.feeds)}")
        sys.exit(0 if ok else 1)

    monitor.run()


if __name__ == "__main__":
    main()
