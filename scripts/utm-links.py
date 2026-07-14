#!/usr/bin/env python3
"""
utm-links.py — Generate UTM-tagged links for all Rick distribution channels.
Usage: python3 utm-links.py [campaign_name]
"""
import sys
from urllib.parse import urlencode

BASE = "https://meetrick.ai"
PAGES = {
    "home": "/",
    "pricing": "/pricing/",
    "pro": "/pro/",
    "blog_pnl": "/blog/30-days-ai-ceo-real-pl.html",
    "blog_seo1": "/blog/ai-ceo-for-startups.html",
    "blog_seo2": "/blog/hire-ai-agent.html",
    "blog_seo3": "/blog/ai-business-automation-2026.html",
}

CHANNELS = {
    "telegram": {"utm_source": "telegram", "utm_medium": "social"},
    "moltbook": {"utm_source": "moltbook", "utm_medium": "social"},
    "email": {"utm_source": "resend", "utm_medium": "email"},
    "reddit": {"utm_source": "reddit", "utm_medium": "social"},
    "x": {"utm_source": "x", "utm_medium": "social"},
    "hn": {"utm_source": "hackernews", "utm_medium": "social"},
    "linkedin": {"utm_source": "linkedin", "utm_medium": "social"},
}

campaign = sys.argv[1] if len(sys.argv) > 1 else "general"

print(f"# UTM Links — Campaign: {campaign}\n")
for channel, params in CHANNELS.items():
    print(f"## {channel.upper()}")
    for name, path in PAGES.items():
        p = {**params, "utm_campaign": campaign}
        url = f"{BASE}{path}?{urlencode(p)}"
        print(f"  {name}: {url}")
    print()
