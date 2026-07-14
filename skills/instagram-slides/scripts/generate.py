#!/usr/bin/env python3
"""
Instagram Slides Generator — Turn content into branded carousels.

Usage:
  python3 generate.py --url <blog-url> [--slides 8] [--output ./slides]
  python3 generate.py --file <markdown-file> [--slides 8] [--output ./slides]
  python3 generate.py --plan-file plan.json [--output ./slides]
  python3 generate.py --url <url> --plan-only [--output ./slides]

Requires: Pillow, requests, Fal API key
"""

import argparse
import json
import os
import sys
import textwrap
from pathlib import Path
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageFont

# Config
SLIDE_SIZE = (1080, 1080)
FONT_PATH = "/System/Library/Fonts/HelveticaNeue.ttc"
FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fonts")

BRAND_FONTS = {
    "display_bold": os.path.join(FONT_DIR, "Fraunces-Bold.ttf"),
    "body_bold": os.path.join(FONT_DIR, "Inter-Bold.ttf"),
    "body_medium": os.path.join(FONT_DIR, "Inter-Medium.ttf"),
    "body_regular": os.path.join(FONT_DIR, "Inter-Regular.ttf"),
}

BRAND = {
    "ink_950": (17, 24, 39),
    "tide_500": (20, 184, 166),
    "tide_400": (45, 212, 191),
    "ember_500": (249, 115, 22),
    "sand_50": (250, 250, 249),
    "sand_100": (245, 245, 244),
}


def get_fal_key():
    for path in [os.path.expanduser("~/.config/fal/api_key")]:
        if os.path.exists(path):
            return open(path).read().strip()
    return os.environ.get("FAL_KEY", "")


def fal_generate_image(prompt, negative_prompt=""):
    """Generate image via Fal Nano Banana Pro."""
    key = get_fal_key()
    if not key:
        print("Error: Fal API key not found", file=sys.stderr)
        sys.exit(1)
    resp = requests.post(
        "https://fal.run/fal-ai/nano-banana-pro",
        json={"prompt": prompt, "image_size": "square_hd", **({"negative_prompt": negative_prompt} if negative_prompt else {})},
        headers={"Authorization": f"Key {key}", "Content-Type": "application/json"},
        timeout=180,
    )
    resp.raise_for_status()
    img_url = resp.json()["images"][0]["url"]
    return requests.get(img_url, timeout=60).content


def load_font(size, role="body"):
    role_map = {"display": "display_bold", "body_bold": "body_bold", "body": "body_medium", "body_light": "body_regular"}
    path = BRAND_FONTS.get(role_map.get(role, "body_medium"), BRAND_FONTS["body_medium"])
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.truetype(FONT_PATH, size, index=0)


def sanitize_text(text):
    for old, new in {"->": ">", "<-": "<", "\u2014": "-", "\u2013": "-", "\u200b": ""}.items():
        text = text.replace(old, new)
    return text.strip()


def wrap_text(text, font, max_width):
    words = text.split()
    lines, current = [], ""
    for word in words:
        test = f"{current} {word}".strip()
        if font.getbbox(test)[2] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def composite_slide(bg_bytes, headline, body, slide_num, total_slides, is_title=False, is_cta=False):
    """Overlay text on background with brand styling."""
    img = Image.open(BytesIO(bg_bytes)).resize(SLIDE_SIZE).convert("RGBA")
    w, h = SLIDE_SIZE
    headline, body = sanitize_text(headline), sanitize_text(body)

    card = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    cdraw = ImageDraw.Draw(card)

    card_margin, card_pad_x, card_pad_y = 48, 40, 32
    max_text_width = w - (card_margin + card_pad_x) * 2

    headline_font = load_font(64 if is_title else 56, role="display")
    body_font = load_font(34, role="body")

    headline_lines = wrap_text(headline, headline_font, max_text_width)
    body_lines = wrap_text(body, body_font, max_text_width) if body else []

    h_line_h = headline_font.getbbox("Ay")[3] + 14
    b_line_h = body_font.getbbox("Ay")[3] + 10

    total_text_h = card_pad_y + 5 + 16 + len(headline_lines) * h_line_h + (16 + len(body_lines) * b_line_h if body_lines else 0) + card_pad_y

    card_x, card_w = card_margin, w - card_margin * 2
    card_y = (h - total_text_h) // 2 if is_title else h - total_text_h - card_margin

    ink = BRAND["ink_950"]
    cdraw.rounded_rectangle([card_x, card_y, card_x + card_w, card_y + total_text_h], radius=20, fill=(ink[0], ink[1], ink[2], 220))

    tide = BRAND["tide_500"]
    bar_y = card_y + card_pad_y
    cdraw.rounded_rectangle([card_x + card_pad_x, bar_y, card_x + card_pad_x + 60, bar_y + 5], radius=2, fill=(tide[0], tide[1], tide[2], 255))

    sand = BRAND["sand_50"]
    y = bar_y + 5 + 16
    for line in headline_lines:
        cdraw.text((card_x + card_pad_x, y), line, font=headline_font, fill=(sand[0], sand[1], sand[2], 255))
        y += h_line_h

    if body_lines:
        y += 16
        sand100 = BRAND["sand_100"]
        for line in body_lines:
            cdraw.text((card_x + card_pad_x, y), line, font=body_font, fill=(sand100[0], sand100[1], sand100[2], 210))
            y += b_line_h

    img = Image.alpha_composite(img, card)

    if not is_title:
        counter_overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        co_draw = ImageDraw.Draw(counter_overlay)
        counter_font = load_font(18, role="body")
        counter = f"{slide_num} / {total_slides}"
        cbbox = counter_font.getbbox(counter)
        cx = card_x + card_w - card_pad_x - (cbbox[2] - cbbox[0])
        cy = card_y + total_text_h - card_pad_y - (cbbox[3] - cbbox[1]) + 4
        co_draw.text((cx, cy), counter, font=counter_font, fill=(tide[0], tide[1], tide[2], 160))
        img = Image.alpha_composite(img, counter_overlay)

    return img.convert("RGB")


def main():
    parser = argparse.ArgumentParser(description="Generate Instagram carousel from content")
    parser.add_argument("--url", help="Blog post URL to extract content from")
    parser.add_argument("--file", help="Local markdown file")
    parser.add_argument("--plan-file", help="Existing plan JSON to generate from")
    parser.add_argument("--plan-only", action="store_true", help="Output plan JSON only, no images")
    parser.add_argument("--slides", type=int, default=8, help="Number of slides (default: 8)")
    parser.add_argument("--style", default="warm editorial", help="Style preset or custom description")
    parser.add_argument("--output", default="./slides", help="Output directory")
    args = parser.parse_args()

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    if args.plan_file:
        plan = json.loads(Path(args.plan_file).read_text())
    elif args.url or args.file:
        if args.url:
            print(f"Fetching {args.url}...")
            content = requests.get(args.url, timeout=30).text[:10000]
        else:
            content = Path(args.file).read_text()[:10000]

        print(f"Planning {args.slides} slides...")
        plan = {
            "slides": [
                {"headline": f"Slide {i+1}", "body": "Content here", "bg_prompt": "warm editorial background, soft lighting", "is_title": i == 0, "is_cta": i == args.slides - 1}
                for i in range(args.slides)
            ],
            "caption": "Your Instagram caption here. #ai #automation"
        }
        print("Note: Auto-planning requires OPENROUTER_API_KEY. Using placeholder plan.")
        print(f"Edit {out / 'plan.json'} and re-run with --plan-file")
    else:
        parser.print_help()
        sys.exit(1)

    (out / "plan.json").write_text(json.dumps(plan, indent=2))
    print(f"Plan saved to {out / 'plan.json'}")

    if args.plan_only:
        return

    slides = plan["slides"]
    for i, slide in enumerate(slides):
        print(f"Generating slide {i+1}/{len(slides)}: {slide['headline']}")
        bg_bytes = fal_generate_image(slide.get("bg_prompt", "warm editorial background"))
        raw_path = out / f"raw_{i+1:02d}.png"
        raw_path.write_bytes(bg_bytes)

        final = composite_slide(bg_bytes, slide["headline"], slide.get("body", ""), i + 1, len(slides), slide.get("is_title", False), slide.get("is_cta", False))
        final_path = out / f"slide_{i+1:02d}.png"
        final.save(str(final_path), "PNG")
        print(f"  Saved {final_path}")

    if plan.get("caption"):
        (out / "caption.txt").write_text(plan["caption"])

    print(f"\nDone! {len(slides)} slides in {out}/")


if __name__ == "__main__":
    main()
