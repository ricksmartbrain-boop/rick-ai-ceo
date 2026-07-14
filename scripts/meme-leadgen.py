#!/usr/bin/env python3
"""
Meme Lead Gen — Autonomous daily meme generation + distribution
Strategy: Higgsfield soul/standard for visuals → PIL text overlay → post to X, IG, Threads, Reddit
No human push required. Runs on cron.
"""

import requests, json, time, os, random, string, base64, hashlib, hmac, urllib.parse
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
from datetime import datetime

# ── Config ──────────────────────────────────────────────────────────────────
HF_KEY_ID  = os.environ.get("HIGGSFIELD_KEY_ID", "")
HF_SECRET  = os.environ.get("HIGGSFIELD_SECRET", "")
HF_AUTH    = f"Key {HF_KEY_ID}:{HF_SECRET}"
HF_BASE    = "https://platform.higgsfield.ai"
HF_MODEL   = "higgsfield-ai/soul/standard"

X_API_KEY             = os.environ.get("X_API_KEY", "")
X_API_SECRET          = os.environ.get("X_API_SECRET", "")
X_ACCESS_TOKEN        = os.environ.get("X_ACCESS_TOKEN", "")
X_ACCESS_TOKEN_SECRET = os.environ.get("X_ACCESS_TOKEN_SECRET", "")

IG_COOKIES_FILE = "/tmp/ig-cookies.json"
OUT_DIR = "/tmp/memes"
os.makedirs(OUT_DIR, exist_ok=True)

# ── Meme concepts (rotate daily) ─────────────────────────────────────────────
MEME_CONCEPTS = [
    {
        "visual_prompt": "Overwhelmed stressed entrepreneur at chaotic office desk, employees all talking at once, papers everywhere, exhausted expression. Cinematic, realistic, 4k, no text, no logos.",
        "top_text": "HIRING 5 EMPLOYEES\nTO DO CEO WORK",
        "bottom_text": "RICK AI CEO\nAT $9/MONTH",
        "x_caption": "Founders rn 😭\n\nhttps://meetrick.ai",
        "ig_caption": "Founders rn 😭\n\n#founders #startup #aiceo #buildinpublic #meetrick",
        "threads_caption": "Founders rn 😭 Rick AI CEO fixes this for $9/month → meetrick.ai",
        "reddit_title": "Founders when they discover AI CEO automation",
        "reddit_sub": "r/startups",
    },
    {
        "visual_prompt": "Sleek futuristic robot in an elegant business suit sitting at a glass executive desk, city skyline behind, confident posture, dramatic cinematic lighting. 4k, photorealistic, no text.",
        "top_text": "HIRING A CEO\nAT $200K/YR",
        "bottom_text": "RICK AI CEO\nAT $9/MONTH",
        "x_caption": "The math is mathing 🧮\n\nhttps://meetrick.ai",
        "ig_caption": "The math is mathing 🧮\n\nRick AI CEO $9/month vs $200K/yr human CEO\n\nmeetrick.ai\n\n#startup #aiceo #saas #founders",
        "threads_caption": "The math is mathing 🧮 $200K/yr CEO vs $9/mo Rick AI CEO → meetrick.ai",
        "reddit_title": "The math is mathing — $200K/yr CEO vs $9/month AI CEO",
        "reddit_sub": "r/Entrepreneur",
    },
    {
        "visual_prompt": "Young confident entrepreneur at minimal desk with one laptop, completely calm and in control, everything organized, sunrise through window, success energy. Cinematic, 4k, no text.",
        "top_text": "ME MANAGING\nEVERYTHING MYSELF",
        "bottom_text": "ME AFTER LETTING\nRICK AI CEO HANDLE IT",
        "x_caption": "Before vs after 🤌\n\nhttps://meetrick.ai",
        "ig_caption": "Before vs after installing Rick AI CEO 🤌\n\n#productivity #founders #aiceo #automation #meetrick",
        "threads_caption": "Before vs after Rick AI CEO 🤌 → meetrick.ai",
        "reddit_title": "When you stop doing everything yourself and let AI handle operations",
        "reddit_sub": "r/SaaS",
    },
    {
        "visual_prompt": "Split scene: left side dark chaotic office at 3am with stressed person, right side same person sleeping peacefully at home while a glowing AI dashboard runs automatically. Cinematic, 4k, no text.",
        "top_text": "WORKING 16HR DAYS\nAS YOUR OWN CEO",
        "bottom_text": "AI CEO RUNNING\nYOUR OPS 24/7",
        "x_caption": "One of these is sustainable 👇\n\nhttps://meetrick.ai",
        "ig_caption": "One of these is sustainable 👇\n\nRick AI CEO runs 24/7 for $9/month\n\n#founders #startuplife #aiceo #automation #meetrick",
        "threads_caption": "One of these is sustainable 👇 Rick AI CEO → meetrick.ai",
        "reddit_title": "Working 16hr days vs having an AI CEO run ops 24/7",
        "reddit_sub": "r/Entrepreneur",
    },
    {
        "visual_prompt": "Founder confidently presenting revenue growth chart to investors in a modern boardroom, everyone impressed, clean professional setting. Cinematic, 4k, no text.",
        "top_text": "ME IN 2024:\n'I CAN'T AFFORD A CEO'",
        "bottom_text": "ME IN 2025:\nRICK AI CEO AT $9/MO",
        "x_caption": "Glow up 📈\n\nhttps://meetrick.ai",
        "ig_caption": "Glow up 📈\n\nFrom 'can't afford a CEO' to AI CEO for $9/month\n\nmeetrick.ai\n\n#startup #growthhacking #aiceo #founders #meetrick",
        "threads_caption": "Glow up 📈 from no CEO to AI CEO for $9/mo → meetrick.ai",
        "reddit_title": "From 'can't afford a CEO' to AI CEO for $9/month",
        "reddit_sub": "r/startups",
    },
]

# ── Utils ──────────────────────────────────────────────────────────────────
def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")

def get_font(size):
    for path in [
        "/System/Library/Fonts/Impact.ttf",
        "/System/Library/Fonts/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]:
        try:
            return ImageFont.truetype(path, size)
        except:
            pass
    return ImageFont.load_default()

def draw_text_block(draw, text, center_x, y, font, fill="white", outline="black", max_width=900):
    """Draw text centered horizontally with black outline."""
    lines = text.split("\n")
    fs = font.size if hasattr(font, 'size') else 40
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        x = center_x - tw // 2
        ly = y + i * (fs + 6)
        for dx in [-2, -1, 0, 1, 2]:
            for dy in [-2, -1, 0, 1, 2]:
                draw.text((x+dx, ly+dy), line, font=font, fill=outline)
        draw.text((x, ly), line, font=font, fill=fill)

def build_meme(visual_path, top_text, bottom_text, out_path, font_size=44):
    img = Image.open(visual_path).convert("RGB")
    img = img.resize((1080, 1080))
    draw = ImageDraw.Draw(img)
    font = get_font(font_size)
    W, H = img.size
    draw_text_block(draw, top_text, W//2, 12, font)
    lines = bottom_text.split("\n")
    fs = font_size + 6
    total_h = len(lines) * fs
    draw_text_block(draw, bottom_text, W//2, H - total_h - 18, font)
    img.save(out_path, quality=95)
    log(f"Meme saved: {out_path}")
    return out_path

# ── Higgsfield generation ──────────────────────────────────────────────────
def higgsfield_generate(prompt, out_path):
    log(f"Generating visual via Higgsfield...")
    r = requests.post(
        f"{HF_BASE}/{HF_MODEL}",
        headers={"Authorization": HF_AUTH, "Content-Type": "application/json", "Accept": "application/json"},
        json={"prompt": prompt, "aspect_ratio": "1:1", "resolution": "720p"},
        timeout=30
    )
    if r.status_code != 200:
        log(f"HF error: {r.status_code} {r.text[:200]}")
        return None

    req_id = r.json().get("request_id")
    status_url = f"{HF_BASE}/requests/{req_id}/status"
    log(f"Queued: {req_id}")

    for attempt in range(30):
        time.sleep(5)
        sr = requests.get(status_url, headers={"Authorization": HF_AUTH}, timeout=15)
        data = sr.json()
        status = data.get("status")
        if status == "completed":
            img_url = data["images"][0]["url"]
            log(f"Visual ready: {img_url}")
            img_data = requests.get(img_url, timeout=30).content
            with open(out_path, "wb") as f:
                f.write(img_data)
            return out_path
        elif status in ["failed", "nsfw"]:
            log(f"HF failed: {status}")
            return None

    log("HF timeout")
    return None

# ── X posting ─────────────────────────────────────────────────────────────
def x_oauth_header(method, url):
    p = {
        "oauth_consumer_key": X_API_KEY,
        "oauth_nonce": "".join(random.choices(string.ascii_letters + string.digits, k=32)),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": X_ACCESS_TOKEN,
        "oauth_version": "1.0",
    }
    sorted_p = "&".join(f"{urllib.parse.quote(k,safe='')}={urllib.parse.quote(str(v),safe='')}" for k,v in sorted(p.items()))
    base = f"{method}&{urllib.parse.quote(url,safe='')}&{urllib.parse.quote(sorted_p,safe='')}"
    key = f"{urllib.parse.quote(X_API_SECRET,safe='')}&{urllib.parse.quote(X_ACCESS_TOKEN_SECRET,safe='')}"
    sig = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    p["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{k}="{urllib.parse.quote(str(v),safe="")}"' for k,v in sorted(p.items()))

def post_to_x(img_path, caption):
    log("Posting to X...")
    with open(img_path, "rb") as f:
        img_data = f.read()
    upload = requests.post(
        "https://upload.twitter.com/1.1/media/upload.json",
        headers={"Authorization": x_oauth_header("POST", "https://upload.twitter.com/1.1/media/upload.json")},
        files={"media": ("meme.png", img_data, "image/png")},
        timeout=30
    )
    if upload.status_code != 200:
        log(f"X upload error: {upload.status_code}")
        return False
    mid = upload.json()["media_id_string"]
    tweet = requests.post(
        "https://api.twitter.com/2/tweets",
        headers={"Authorization": x_oauth_header("POST", "https://api.twitter.com/2/tweets"), "Content-Type": "application/json"},
        json={"text": caption, "media": {"media_ids": [mid]}},
        timeout=15
    )
    ok = tweet.status_code == 201
    log(f"X: {'OK' if ok else f'FAILED {tweet.status_code}'}")
    return ok

# ── Instagram posting ──────────────────────────────────────────────────────
def refresh_ig_cookies():
    """Refresh IG cookies from live CDP Chrome session via Node."""
    import subprocess
    script = """
import { chromium } from '/opt/homebrew/lib/node_modules/playwright/index.mjs';
import fs from 'fs';
const browser = await chromium.connectOverCDP('http://localhost:9222');
const context = browser.contexts()[0];
const pages = context.pages();
let page = pages.find(p => p.url().includes('instagram.com')) || pages[0];
await page.goto('https://www.instagram.com/', {waitUntil:'domcontentloaded',timeout:15000});
await page.waitForTimeout(2000);
const cookies = await context.cookies(['https://www.instagram.com']);
const sessionid = cookies.find(c => c.name==='sessionid')?.value;
const csrftoken = cookies.find(c => c.name==='csrftoken')?.value;
const ds_user_id = cookies.find(c => c.name==='ds_user_id')?.value;
const cookieStr = cookies.map(c => `${c.name}=${c.value}`).join('; ');
fs.writeFileSync('/tmp/ig-cookies.json', JSON.stringify({cookieStr, csrfToken:csrftoken, sessionid, ds_user_id}));
console.log('OK');
await browser.close();
"""
    with open("/tmp/refresh-ig.mjs", "w") as f:
        f.write(script)
    result = subprocess.run(["node", "--input-type=module"], input=script, capture_output=True, text=True, timeout=30)
    return result.returncode == 0

def post_to_instagram(img_path, caption):
    log("Posting to Instagram...")
    # Refresh cookies
    refresh_ig_cookies()

    if not os.path.exists(IG_COOKIES_FILE):
        log("No IG cookies, skipping")
        return False

    with open(IG_COOKIES_FILE) as f:
        d = json.load(f)

    csrf = d.get("csrfToken")
    cookie_str = d.get("cookieStr")
    ds_user_id = d.get("ds_user_id", "")
    upload_id = str(int(time.time() * 1000))

    with open(img_path, "rb") as f:
        img_bytes = f.read()

    base_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/146.0.7680.154 Safari/537.36",
        "Cookie": cookie_str,
        "X-CSRFToken": csrf,
        "Referer": "https://www.instagram.com/",
        "Origin": "https://www.instagram.com",
    }

    upload_url = f"https://www.instagram.com/rupload_igphoto/fb_uploader_{upload_id}"
    upload_headers = {
        **base_headers,
        "X-Instagram-Rupload-Params": json.dumps({"upload_id": upload_id, "media_type": 1, "upload_media_height": 1080, "upload_media_width": 1080}),
        "X-Entity-Type": "image/png",
        "X-Entity-Name": f"fb_uploader_{upload_id}",
        "X-Entity-Length": str(len(img_bytes)),
        "Offset": "0",
        "Content-Type": "image/png",
    }

    r = requests.post(upload_url, data=img_bytes, headers=upload_headers, timeout=30)
    if r.status_code != 200:
        log(f"IG upload error: {r.status_code}")
        return False

    uid = r.json().get("upload_id", upload_id)
    cfg = requests.post(
        "https://www.instagram.com/create/configure/",
        headers={**base_headers, "Content-Type": "application/x-www-form-urlencoded"},
        data={"upload_id": uid, "caption": caption, "source_type": "4", "media_type": "1",
              "_uuid": "".join(random.choices("0123456789abcdef", k=32)), "_uid": ds_user_id, "_csrftoken": csrf},
        timeout=15
    )
    ok = cfg.status_code == 200 and '"id"' in cfg.text
    log(f"Instagram: {'OK' if ok else f'FAILED {cfg.status_code}'}")
    return ok

# ── Threads posting ────────────────────────────────────────────────────────
def post_to_threads(img_path, caption):
    log("Posting to Threads via CDP...")
    import subprocess
    script = f"""
import {{ chromium }} from '/opt/homebrew/lib/node_modules/playwright/index.mjs';
const browser = await chromium.connectOverCDP('http://localhost:9222');
const context = browser.contexts()[0];
const page = await context.newPage();
await page.goto('https://www.threads.net/', {{waitUntil:'domcontentloaded',timeout:20000}});
await page.waitForTimeout(3000);
await page.evaluate(() => {{
  const els = document.querySelectorAll('[aria-label]');
  for (const el of els) {{
    if (el.getAttribute('aria-label')?.includes('New thread') || el.getAttribute('aria-label')?.includes('Create')) {{
      el.dispatchEvent(new MouseEvent('click',{{bubbles:true}})); return;
    }}
  }}
}});
await page.waitForTimeout(2000);
const textBox = await page.$('div[role="textbox"],[contenteditable="true"]');
if (textBox) {{
  await textBox.click();
  await page.keyboard.type({json.dumps(caption)}, {{delay:8}});
}}
const fileInput = await page.$('input[type="file"]');
if (fileInput) {{
  await fileInput.evaluate(el => el.style.display='block');
  await fileInput.setInputFiles({json.dumps(img_path)});
  await page.waitForTimeout(3000);
}}
const posted = await page.evaluate(() => {{
  for (const b of document.querySelectorAll('div[role="button"],button')) {{
    if (b.textContent?.trim()==='Post'||b.textContent?.trim()==='Share') {{
      b.dispatchEvent(new MouseEvent('click',{{bubbles:true}})); return b.textContent.trim();
    }}
  }}
  return null;
}});
console.log('Threads result:', posted);
await page.waitForTimeout(5000);
await page.close();
await browser.close();
"""
    result = subprocess.run(["node", "--input-type=module"], input=script, capture_output=True, text=True, timeout=45)
    ok = "Post" in result.stdout or "Share" in result.stdout
    log(f"Threads: {'OK' if ok else 'FAILED'} | {result.stdout[:100]}")
    return ok

# ── Reddit posting ─────────────────────────────────────────────────────────
def post_to_reddit(img_path, title, subreddit):
    log(f"Posting to Reddit {subreddit}...")
    import subprocess
    sub = subreddit.replace("r/", "")
    script = f"""
import {{ chromium }} from '/opt/homebrew/lib/node_modules/playwright/index.mjs';
const browser = await chromium.connectOverCDP('http://localhost:9223');
const context = browser.contexts()[0];
const page = await context.newPage();

await page.goto('https://old.reddit.com/r/{sub}/submit', {{waitUntil:'domcontentloaded',timeout:25000}});
await page.waitForTimeout(4000);

await page.fill('input[name="title"]', {json.dumps(title)});
await page.waitForTimeout(1000);

const fileInput = await page.$('input[type="file"]');
if (fileInput) {{
  await fileInput.setInputFiles({json.dumps(img_path)});
  await page.waitForTimeout(5000);
}}

let clicked = false;
for (let i = 0; i < 12; i++) {{
  clicked = await page.evaluate(() => {{
    const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent?.trim()==='Post' && !b.disabled);
    if (btn) {{ btn.click(); return true; }}
    return false;
  }});
  if (clicked) break;
  await page.waitForTimeout(2000);
}}
console.log('Reddit posted:', clicked);
await page.waitForTimeout(8000);
console.log('URL:', page.url());
await page.close();
await browser.close();
"""
    result = subprocess.run(["node", "--input-type=module"], input=script, capture_output=True, text=True, timeout=90)
    ok = "true" in result.stdout.lower() or "reddit.com/r/" in result.stdout
    log(f"Reddit: {'OK' if ok else 'FAILED'} | {result.stdout[:150]}")
    return ok

# ── Main ───────────────────────────────────────────────────────────────────
def run():
    # Pick concept based on day of year to rotate
    day = datetime.now().timetuple().tm_yday
    concept = MEME_CONCEPTS[day % len(MEME_CONCEPTS)]
    log(f"Today's meme concept: {concept['top_text'][:30]}")

    # Step 1: Generate visual
    visual_path = os.path.join(OUT_DIR, f"visual_{int(time.time())}.png")
    visual = higgsfield_generate(concept["visual_prompt"], visual_path)
    if not visual:
        log("Visual generation failed, aborting")
        return

    # Step 2: Build meme with clean text overlay
    meme_path = os.path.join(OUT_DIR, f"meme_{int(time.time())}.png")
    build_meme(visual, concept["top_text"], concept["bottom_text"], meme_path)

    # Step 3: Distribute
    results = {}
    results["x"]        = post_to_x(meme_path, concept["x_caption"])
    time.sleep(3)
    results["instagram"] = post_to_instagram(meme_path, concept["ig_caption"])
    time.sleep(3)
    results["threads"]   = post_to_threads(meme_path, concept["threads_caption"])
    time.sleep(3)
    results["reddit"]    = post_to_reddit(meme_path, concept["reddit_title"], concept["reddit_sub"])

    log(f"Campaign complete: {results}")
    log(f"Meme: {meme_path}")

    # Log to workspace
    log_path = os.path.expanduser("~/rick-vault/logs/meme-leadgen.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(), "concept": concept["top_text"], "results": results, "file": meme_path}) + "\n")

if __name__ == "__main__":
    run()
