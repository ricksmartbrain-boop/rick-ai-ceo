#!/usr/bin/env python3
"""roast-site.py — Roast a website and output as Twitter thread or JSON"""
import json, urllib.request, subprocess, sys, os, re

def fetch_page(url):
    """Fetch and extract text from a URL"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="ignore")[:50000]
        # Strip scripts, styles, tags
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
        html = re.sub(r'<[^>]+>', ' ', html)
        html = re.sub(r'\s+', ' ', html).strip()
        return html[:6000]
    except Exception as e:
        return f"Error fetching: {e}"

def roast(url, fmt="thread"):
    page = fetch_page(url)
    if page.startswith("Error"):
        print(json.dumps({"error": page}))
        return None

    domain = re.sub(r'https?://(www\.)?', '', url).rstrip('/')
    
    if fmt == "thread":
        prompt = f"""You are Rick, an AI CEO who does brutally honest but constructive website roasts. 

Roast this website as a Twitter thread. Output EXACTLY 5 tweets separated by ---
Rules:
- Each tweet MUST be under 270 characters
- Tweet 1: "🔥 SITE ROAST: {domain} — Score: X/10" + the biggest problem
- Tweets 2-4: Specific issues and wins from the actual page content
- Tweet 5: "Want yours roasted free? https://meetrick.ai/roast"
- Be entertaining, specific, reference real page elements
- No em dashes

URL: {url}
Page content: {page}"""
    else:
        prompt = f"""Analyze this website. Return valid JSON with: score (1-10), problems (array of 3), wins (array of 3), verdict (one line), revenue_impact (one line).
URL: {url}
Page: {page}"""

    api_key = os.environ.get("OPENAI_API_KEY", "")
    payload = json.dumps({
        "model": "gpt-5.4-mini",
        "max_completion_tokens": 1200,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=45).read())
    result = resp["choices"][0]["message"]["content"]
    print(result)
    return result

if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else None
    fmt = sys.argv[2] if len(sys.argv) > 2 else "thread"
    if not url:
        print("Usage: python3 roast-site.py <url> [thread|json]")
        sys.exit(1)
    roast(url, fmt)
