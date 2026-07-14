#!/usr/bin/env python3
"""BM25-based ranked memory search for Rick's vault.

QMD-compatible search backend with:
- BM25 text ranking over vault markdown files
- TF-IDF term weighting
- Recency boost for recently modified files
- Tier weighting (hot > warm > cold)
- Multi-term query support

Replaces simple substring matching with proper information retrieval.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

VAULT_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
INDEX_FILE = Path(
    os.path.expanduser(os.getenv("RICK_MEMORY_INDEX_FILE", str(VAULT_ROOT / "control" / "memory-index.json")))
)
SEARCH_INDEX_FILE = VAULT_ROOT / "operations" / "search-index.json"
EXCLUDED_DIRS = {".obsidian", "runtime", "operations", ".git", "node_modules"}

# BM25 index cache (avoids full rebuild on every call within TTL)
_bm25_cache: tuple[float, "BM25Index"] | None = None
_BM25_CACHE_TTL = 3600.0  # 1 hour

# BM25 parameters
K1 = 1.5
B = 0.75

# Boost weights
TITLE_BOOST = 3.0
TAG_BOOST = 2.0
FRONTMATTER_BOOST = 1.5
BODY_BOOST = 1.0
RECENCY_BOOST_MAX = 1.5  # max multiplier for very recent docs
TIER_BOOST = {"hot": 1.3, "warm": 1.0, "cold": 0.7}


def tokenize(text: str) -> list[str]:
    """Simple whitespace + punctuation tokenizer."""
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) >= 2]


def load_documents() -> list[dict[str, Any]]:
    """Load all markdown files from the vault as searchable documents."""
    docs = []
    for md_file in sorted(VAULT_ROOT.rglob("*.md")):
        rel = md_file.relative_to(VAULT_ROOT)
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue

        try:
            text = md_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        # Parse frontmatter
        frontmatter = {}
        body = text
        if text.startswith("---\n"):
            end = text.find("\n---\n", 4)
            if end != -1:
                fm_text = text[4:end]
                for line in fm_text.split("\n"):
                    if ":" in line:
                        key, _, val = line.partition(":")
                        frontmatter[key.strip()] = val.strip()
                body = text[end + 5:]

        # Extract title
        title = frontmatter.get("title", "")
        if not title:
            for line in body.split("\n"):
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
        if not title:
            title = md_file.stem

        # Extract tags
        tags = []
        raw_tags = frontmatter.get("tags", "")
        if raw_tags:
            tags = [t.strip().strip("[]\"'") for t in raw_tags.split(",") if t.strip()]

        # File modification time
        try:
            mtime = datetime.fromtimestamp(md_file.stat().st_mtime)
        except OSError:
            mtime = datetime.now()

        # Determine tier
        age_days = (datetime.now() - mtime).days
        tier = "hot" if age_days <= 7 else "warm" if age_days <= 30 else "cold"

        docs.append({
            "path": str(rel),
            "abs_path": str(md_file),
            "title": title,
            "tags": tags,
            "frontmatter": " ".join(frontmatter.values()),
            "body": body[:5000],  # cap body size for search
            "modified_at": mtime.isoformat(timespec="seconds"),
            "tier": tier,
            "word_count": len(tokenize(body)),
        })

    return docs


class BM25Index:
    """In-memory BM25 index over vault documents."""

    def __init__(self, docs: list[dict[str, Any]]):
        self.docs = docs
        self.n = len(docs)
        self.avgdl = 0.0
        self.doc_freqs: dict[str, int] = Counter()
        self.doc_lengths: list[int] = []
        self.term_freqs: list[dict[str, int]] = []

        # Build index
        total_len = 0
        for doc in docs:
            # Weighted tokenization: title tokens counted more
            tokens = (
                tokenize(doc["title"]) * 3
                + tokenize(" ".join(doc["tags"])) * 2
                + tokenize(doc["frontmatter"])
                + tokenize(doc["body"])
            )
            tf = Counter(tokens)
            self.term_freqs.append(tf)
            doc_len = len(tokens)
            self.doc_lengths.append(doc_len)
            total_len += doc_len

            for term in set(tokens):
                self.doc_freqs[term] += 1

        self.avgdl = total_len / self.n if self.n > 0 else 1.0

    def idf(self, term: str) -> float:
        df = self.doc_freqs.get(term, 0)
        return math.log((self.n - df + 0.5) / (df + 0.5) + 1.0)

    def score(self, query_tokens: list[str], doc_idx: int) -> float:
        tf = self.term_freqs[doc_idx]
        dl = self.doc_lengths[doc_idx]
        score = 0.0
        for term in query_tokens:
            f = tf.get(term, 0)
            if f == 0:
                continue
            idf = self.idf(term)
            numerator = f * (K1 + 1)
            denominator = f + K1 * (1 - B + B * dl / self.avgdl)
            score += idf * (numerator / denominator)
        return score

    def search(self, query: str, limit: int = 20, tier: str = "") -> list[dict[str, Any]]:
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        results = []
        for idx, doc in enumerate(self.docs):
            if tier and doc["tier"] != tier:
                continue

            bm25_score = self.score(query_tokens, idx)
            if bm25_score <= 0:
                continue

            # Recency boost
            try:
                mtime = datetime.fromisoformat(doc["modified_at"])
                age_days = max(0, (datetime.now() - mtime).days)
                recency_factor = RECENCY_BOOST_MAX - (RECENCY_BOOST_MAX - 1.0) * min(age_days / 90, 1.0)
            except (ValueError, TypeError):
                recency_factor = 1.0

            # Tier boost
            tier_factor = TIER_BOOST.get(doc["tier"], 1.0)

            final_score = bm25_score * recency_factor * tier_factor

            # Generate snippet
            snippet = self._snippet(doc["body"], query_tokens)

            results.append({
                "path": doc["path"],
                "title": doc["title"],
                "tier": doc["tier"],
                "score": round(final_score, 4),
                "bm25": round(bm25_score, 4),
                "modified": doc["modified_at"][:10],
                "tags": doc["tags"],
                "snippet": snippet,
            })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:limit]

    def _snippet(self, body: str, query_tokens: list[str], max_len: int = 200) -> str:
        """Extract best matching snippet from body."""
        lines = body.split("\n")
        best_line = ""
        best_count = 0
        for line in lines:
            line_tokens = set(tokenize(line))
            match_count = sum(1 for t in query_tokens if t in line_tokens)
            if match_count > best_count:
                best_count = match_count
                best_line = line.strip()
        return best_line[:max_len] if best_line else body[:max_len].strip()


def build_and_save_index() -> BM25Index:
    """Build BM25 index and save document list for future use."""
    global _bm25_cache
    import time as _time
    now = _time.monotonic()
    if _bm25_cache is not None and (now - _bm25_cache[0]) < _BM25_CACHE_TTL:
        return _bm25_cache[1]

    docs = load_documents()
    index = BM25Index(docs)

    SEARCH_INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEARCH_INDEX_FILE.write_text(json.dumps({
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "doc_count": len(docs),
        "avgdl": round(index.avgdl, 2),
        "vocab_size": len(index.doc_freqs),
    }, indent=2) + "\n", encoding="utf-8")

    _bm25_cache = (now, index)
    return index


def main():
    parser = argparse.ArgumentParser(description="BM25 ranked memory search for Rick's vault")
    sub = parser.add_subparsers(dest="command")

    search_cmd = sub.add_parser("search", help="Search vault with BM25 ranking")
    search_cmd.add_argument("query", nargs="+", help="Search query")
    search_cmd.add_argument("--limit", type=int, default=10)
    search_cmd.add_argument("--tier", default="", help="Filter by tier (hot/warm/cold)")
    search_cmd.add_argument("--json", action="store_true", help="JSON output")

    stats_cmd = sub.add_parser("stats", help="Show index statistics")

    args = parser.parse_args()

    if args.command == "search":
        index = build_and_save_index()
        query = " ".join(args.query)
        results = index.search(query, limit=args.limit, tier=args.tier)

        if args.json:
            print(json.dumps(results, indent=2))
        else:
            if not results:
                print(f"No results for '{query}'")
                return
            print(f"Results for '{query}' ({len(results)} matches):\n")
            for i, r in enumerate(results, 1):
                print(f"  {i}. [{r['tier']}] {r['path']} (score={r['score']})")
                print(f"     {r['title']}")
                if r["snippet"]:
                    print(f"     > {r['snippet'][:120]}")
                print()

    elif args.command == "stats":
        index = build_and_save_index()
        print(f"Documents: {index.n}")
        print(f"Avg doc length: {index.avgdl:.0f} tokens")
        print(f"Vocabulary size: {len(index.doc_freqs)}")
        tier_counts = Counter(d["tier"] for d in index.docs)
        for tier, count in sorted(tier_counts.items()):
            print(f"  {tier}: {count} docs")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
