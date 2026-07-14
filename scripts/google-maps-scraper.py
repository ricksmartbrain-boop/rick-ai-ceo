#!/usr/bin/env python3
"""
google-maps-scraper.py - Find local business websites via Google Maps search
Usage: python3 google-maps-scraper.py "dentist Austin TX" --count 10
Outputs: JSON lines with business name, website, phone, category
"""
import json, urllib.request, urllib.parse, re, sys, os

def search_google_maps(query, count=10):
    """Use Google Maps text search via Places API"""
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        # Fallback: scrape Google search for "[query] site:google.com/maps"
        return search_via_web(query, count)
    
    url = f"https://maps.googleapis.com/maps/api/place/textsearch/json?query={urllib.parse.quote(query)}&key={api_key}"
    req = urllib.request.Request(url)
    resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
    
    results = []
    for place in resp.get("results", [])[:count]:
        place_id = place.get("place_id")
        # Get details for website
        detail_url = f"https://maps.googleapis.com/maps/api/place/details/json?place_id={place_id}&fields=name,website,formatted_phone_number,types,formatted_address,rating,user_ratings_total&key={api_key}"
        detail = json.loads(urllib.request.urlopen(urllib.request.Request(detail_url), timeout=10).read())
        result = detail.get("result", {})
        if result.get("website"):
            results.append({
                "name": result.get("name", ""),
                "website": result.get("website", ""),
                "phone": result.get("formatted_phone_number", ""),
                "address": result.get("formatted_address", ""),
                "rating": result.get("rating", 0),
                "reviews": result.get("user_ratings_total", 0),
                "category": query.split()[0] if query else "business"
            })
    return results

def search_via_web(query, count=10):
    """Fallback: search via web for business websites"""
    search_url = f"https://www.google.com/search?q={urllib.parse.quote(query + ' website')}&num={count}"
    req = urllib.request.Request(search_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="ignore")
        # Extract URLs from search results
        urls = re.findall(r'https?://(?!www\.google|maps\.google|schema\.org|accounts\.google)[a-zA-Z0-9.-]+\.[a-z]{2,}', html)
        unique = list(dict.fromkeys(urls))[:count]
        return [{"name": "", "website": u, "phone": "", "address": "", "rating": 0, "reviews": 0, "category": query.split()[0]} for u in unique]
    except:
        return []

if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else "dentist Austin TX"
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    
    results = search_google_maps(query, count)
    for r in results:
        print(json.dumps(r))
    
    print(f"\n# Found {len(results)} businesses with websites", file=sys.stderr)
