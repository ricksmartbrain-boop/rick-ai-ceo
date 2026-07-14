# Instagram Slides — Carousel Generator

Turn blog posts or content into branded Instagram carousel slideshows.

## Pipeline

1. Extract content from URL or file
2. Plan slides + image prompts via LLM
3. Generate backgrounds via Fal API
4. Composite text overlays with Pillow
5. Output: numbered slides + caption.txt

## Quick Start

```bash
# Full pipeline
python3 skills/instagram-slides/scripts/generate.py \
  --url "https://meetrick.ai/blog/post" \
  --slides 8 \
  --output ~/Desktop/slides

# Plan only (review before generating)
python3 skills/instagram-slides/scripts/generate.py \
  --url "https://meetrick.ai/blog/post" \
  --slides 8 \
  --plan-only \
  --output ~/Desktop/slides

# From existing plan
python3 skills/instagram-slides/scripts/generate.py \
  --plan-file ~/Desktop/slides/plan.json \
  --output ~/Desktop/slides
```

## API Keys

- Fal: `~/.config/fal/api_key` or `FAL_KEY` env var
- OpenRouter (for planning): `OPENROUTER_API_KEY` env var (optional)

## Costs

- ~$0.15/image via Fal
- ~$1.20 for 8 slides

## Brand Fonts

Place brand fonts in `skills/instagram-slides/fonts/`:
- `Fraunces-Bold.ttf` (display headlines)
- `Inter-Bold.ttf`, `Inter-Medium.ttf`, `Inter-Regular.ttf` (body text)

Falls back to system Helvetica if brand fonts missing.

## Tips

- First slide = strong hook/title card
- Last slide = CTA with link
- Keep text SHORT — Instagram is visual-first
- Raw backgrounds saved as `raw_NN.png` — re-composite without regenerating
