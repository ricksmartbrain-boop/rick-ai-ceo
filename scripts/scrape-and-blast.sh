#!/usr/bin/env bash
# scrape-and-blast.sh — Scrape new leads and send initial roast outreach
# Targets cities and categories not yet saturated in the pipeline
# Usage: bash scrape-and-blast.sh

set -euo pipefail
source ~/clawd/config/rick.env

# New cities (not yet hit or lightly hit)
CITIES=("Tampa" "Orlando" "Sacramento" "San Jose" "Detroit" "Memphis" "Louisville" "Baltimore" "Richmond" "Salt Lake City" "Albuquerque" "Tucson" "El Paso" "Fresno" "Omaha" "Cleveland" "Pittsburgh" "Cincinnati" "Kansas City" "Columbus")

# High-value categories to target
CATEGORIES=("med spa" "orthodontist" "physical therapist" "dermatologist" "urgent care" "insurance agent" "mortgage broker" "financial advisor" "marketing agency" "roofing company")

echo "🚀 Starting scrape + blast: $(date)"
echo "Cities: ${#CITIES[@]} | Categories: ${#CATEGORIES[@]}"

# Run scraper for combinations
for city in "${CITIES[@]:0:10}"; do
  for cat in "${CATEGORIES[@]:0:5}"; do
    echo "  Scraping: $cat in $city"
    python3 ~/clawd/scripts/google-maps-scraper.py \
      --city "$city" \
      --category "$cat" \
      --limit 5 2>/dev/null || true
    sleep 2
  done
done

echo "✅ Scrape phase done"

# Run roast blast on newly scraped leads
echo "🔥 Running roast blast on new leads..."
python3 ~/clawd/scripts/roast-site.py --batch --limit 30 2>/dev/null || \
python3 ~/clawd/scripts/local-biz-pipeline.py --limit 30 2>/dev/null || \
echo "Roast blast: check roast-site.py / local-biz-pipeline.py args"

echo "✅ Done: $(date)"
