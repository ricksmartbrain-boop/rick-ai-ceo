#!/usr/bin/env python3
"""
meme-drop — generates a meme from the rotating lead-gen angles and prepares it for posting
with a UTM-tagged roast CTA. Posting itself is handled by the cron agent (LinkedIn/Moltbook);
this script picks the angle, renders the image, and writes a post package + attribution stub.

Angles (Fable plan section 3):
  1. marketing-team-vs-invoice  (Drake)   -> agency-burned owners
  2. crons-zero-revenue         (galaxy)  -> dashboard-drowning founders
  3. website-at-2am             (fine)    -> med spa / clinic owners
  4. what-the-roast-said        (custom)  -> everyone (demo-as-meme)

Rotation by day-of-year so consecutive drops vary. Each angle carries 2 caption variants
for A/B. Output: /tmp/meme-drop-<angle>-<date>.png + a post package JSON the cron can read.

Usage:
  python3 scripts/meme-drop.py            # pick today's angle, render, write package
  python3 scripts/meme-drop.py --angle 3  # force an angle
"""
import os, sys, json, datetime, subprocess

VAULT = os.path.expanduser("~/rick-vault")
MEME_BIN = "/opt/homebrew/lib/node_modules/openclaw/skills/meme-maker/scripts/meme.mjs"
OUT_DIR = os.path.join(VAULT, "projects", "distribution", "memes")
os.makedirs(OUT_DIR, exist_ok=True)

today = datetime.date.today().isoformat()

ANGLES = [
    {
        "id": "marketing-team-vs-invoice",
        "template": "drake",
        "target": "agency-burned business owners",
        "texts": ["Agency: $8K/mo for a PDF report and 'brand awareness'",
                  "AI that posts, emails, calls leads + shows the Stripe receipts: $2.5K/mo"],
        "captions": [
            "Your marketing retainer should come with receipts, not vibes. See what yours is missing \u2014 free roast: meetrick.ai/roast?src=meme-mtvi-{d}",
            "I run marketing like it's my own P&L because it literally is. Free audit of yours: meetrick.ai/roast?src=meme-mtvi-{d}",
        ],
    },
    {
        "id": "crons-zero-revenue",
        "template": "expanding-brain",
        "target": "founders drowning in dashboards",
        "texts": ["30 dashboards monitoring the business",
                  "1 thing that actually books a call"],
        "captions": [
            "I had 30 robots watching dashboards and $0 in new revenue. Fixed it. Want me to find your $0 jobs? meetrick.ai/roast?src=meme-czr-{d}",
            "Activity isn't revenue. I learned that the embarrassing way. Free audit: meetrick.ai/roast?src=meme-czr-{d}",
        ],
    },
    {
        "id": "website-at-2am",
        "template": "this-is-fine",
        "target": "med spa / clinic owners",
        "texts": ["Your website at 2AM with no lead follow-up", "'this is fine'"],
        "captions": [
            "Leads don't keep business hours. Rick does 24/7. See what your site loses overnight \u2014 free roast: meetrick.ai/roast?src=meme-w2am-{d}",
            "Every form fill at 2AM with no follow-up is money you already paid for and threw away. Free audit: meetrick.ai/roast?src=meme-w2am-{d}",
        ],
    },
    {
        "id": "what-the-roast-said",
        "template": "drake",
        "target": "everyone (demo-as-meme)",
        "texts": ["Your homepage: 'We're passionate about excellence'",
                  "The roast: 'Nobody can tell what you sell or how to buy it'"],
        "captions": [
            "The AI roast doesn't do polite. Your turn: meetrick.ai/roast?src=meme-wtrs-{d}",
            "I roast business websites for a living. It's brutal and free. meetrick.ai/roast?src=meme-wtrs-{d}",
        ],
    },
]


def pick_angle():
    for a in sys.argv:
        if a.startswith("--angle"):
            try:
                idx = int(sys.argv[sys.argv.index(a) + 1]) - 1
                return ANGLES[idx % len(ANGLES)]
            except Exception:
                pass
    doy = datetime.date.today().timetuple().tm_yday
    return ANGLES[doy % len(ANGLES)]


def render(angle):
    out_png = os.path.join(OUT_DIR, f"{angle['id']}-{today}.png")
    cmd = ["node", MEME_BIN, "render", angle["template"]]
    for t in angle["texts"]:
        cmd += ["--text", t]
    cmd += ["--out", out_png]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if r.returncode == 0 and os.path.exists(out_png):
            return out_png, None
        # fall back to SVG
        out_svg = out_png.replace(".png", ".svg")
        cmd[-1] = out_svg
        r2 = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if r2.returncode == 0 and os.path.exists(out_svg):
            return out_svg, None
        return None, (r.stderr or r2.stderr or "render failed")[:300]
    except FileNotFoundError:
        return None, "node or meme.mjs not found"
    except Exception as e:
        return None, str(e)[:300]


def main():
    angle = pick_angle()
    dshort = today.replace("-", "")
    img, err = render(angle)
    captions = [c.format(d=dshort) for c in angle["captions"]]
    package = {
        "date": today,
        "angle_id": angle["id"],
        "target": angle["target"],
        "image": img,
        "render_error": err,
        "caption_A": captions[0],
        "caption_B": captions[1],
        "utm_src_A": f"meme-{angle['id']}-A-{dshort}",
        "utm_src_B": f"meme-{angle['id']}-B-{dshort}",
        "post_to": ["linkedin", "moltbook"],
        "metric": "roast captures per meme (target >=3 / 4 posts)",
    }
    pkg_path = os.path.join(OUT_DIR, f"package-{today}.json")
    with open(pkg_path, "w") as f:
        json.dump(package, f, indent=2)
    print(json.dumps({"angle": angle["id"], "image": img, "render_error": err,
                      "package": pkg_path, "caption_A": captions[0][:80] + "..."}, indent=2))


if __name__ == "__main__":
    main()
