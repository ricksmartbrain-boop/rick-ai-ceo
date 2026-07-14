# Blog Image Generator — Gemini Hero Images

Generate hero images for blog posts using Google Gemini.

## Quick Start

```bash
bash skills/blog-image-generator/scripts/generate.sh \
  "laptop showing analytics dashboard on a clean desk" \
  ~/Desktop/hero.png
```

## API Key

Set at `~/.config/gemini/api_key` or `GEMINI_API_KEY` env var.

## Style Tips

Good prompts:
- "Bright workspace with laptop showing analytics, warm natural lighting"
- "Overhead flat-lay of coffee and notebook on marble, soft morning light"
- "Abstract geometric pattern in teal and earth tones, modern minimalist"

Always include: lighting direction, photographic style. Always avoid: baked-in text (Gemini renders text poorly).
