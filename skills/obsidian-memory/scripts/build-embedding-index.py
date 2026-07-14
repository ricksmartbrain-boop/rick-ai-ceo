#!/usr/bin/env python3
"""Build semantic embedding index over Rick's vault memory index.

Reads the existing memory-index.json (built by rebuild-memory-index.py),
embeds each entry's searchable text using sentence-transformers, and writes
a compact numpy index + metadata sidecar for fast cosine search.

Usage:
    python3 build-embedding-index.py [--force]

The index is incremental: entries whose content hash hasn't changed are skipped.
Pass --force to rebuild all embeddings from scratch.

Output files (in $RICK_DATA_ROOT/control/):
    semantic-index.npz    — numpy array of embeddings (N x 384)
    semantic-meta.json    — parallel array of {path, title, type, project, tier, preview, content_hash}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

VAULT_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
INDEX_FILE = VAULT_ROOT / "control" / "memory-index.json"
EMBEDDING_FILE = VAULT_ROOT / "control" / "semantic-index.npz"
META_FILE = VAULT_ROOT / "control" / "semantic-meta.json"

# Embedding model — ~80MB, runs locally on ARM Mac, 384 dimensions
MODEL_NAME = "all-MiniLM-L6-v2"
MAX_TEXT_LENGTH = 2000  # chars per entry for embedding (keeps cost/time bounded)


def load_memory_index() -> list[dict[str, Any]]:
    """Load the existing memory-index.json."""
    if not INDEX_FILE.exists():
        print(f"ERROR: Memory index not found at {INDEX_FILE}", file=sys.stderr)
        print("Run: python3 rebuild-memory-index.py rebuild --write", file=sys.stderr)
        sys.exit(1)
    data = json.loads(INDEX_FILE.read_text("utf-8"))
    return data.get("entries", [])


def build_searchable_text(entry: dict[str, Any]) -> str:
    """Build the text blob that gets embedded for each entry."""
    parts = [
        entry.get("title", ""),
        entry.get("type", ""),
        entry.get("project", ""),
        entry.get("preview", ""),
        " ".join(entry.get("tags", [])),
        " ".join(entry.get("wikilinks", [])),
    ]
    # Also read the actual file content for richer embeddings
    file_path = VAULT_ROOT / entry["path"]
    if file_path.exists() and file_path.is_file():
        try:
            content = file_path.read_text("utf-8")
            # Strip frontmatter
            if content.startswith("---\n"):
                end = content.find("\n---\n", 4)
                if end != -1:
                    content = content[end + 5:]
            parts.append(content[:MAX_TEXT_LENGTH])
        except (OSError, UnicodeDecodeError):
            pass
    text = " ".join(p for p in parts if p).strip()
    return text[:MAX_TEXT_LENGTH]


def content_hash(text: str) -> str:
    """Fast hash of the text content for incremental updates."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]


def load_existing_meta() -> dict[str, dict]:
    """Load existing metadata keyed by path for incremental updates."""
    if not META_FILE.exists():
        return {}
    try:
        meta_list = json.loads(META_FILE.read_text("utf-8"))
        return {m["path"]: m for m in meta_list}
    except (json.JSONDecodeError, KeyError):
        return {}


def load_existing_embeddings() -> np.ndarray | None:
    """Load existing embeddings array."""
    if not EMBEDDING_FILE.exists():
        return None
    try:
        data = np.load(EMBEDDING_FILE)
        return data["embeddings"]
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description="Build semantic embedding index")
    parser.add_argument("--force", action="store_true", help="Rebuild all embeddings from scratch")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output")
    args = parser.parse_args()

    t0 = time.time()
    entries = load_memory_index()
    if not entries:
        print("No entries in memory index.", file=sys.stderr)
        sys.exit(1)

    # Build searchable texts and hashes
    texts = []
    meta = []
    for entry in entries:
        text = build_searchable_text(entry)
        h = content_hash(text)
        texts.append(text)
        meta.append({
            "path": entry["path"],
            "title": entry.get("title", ""),
            "type": entry.get("type", ""),
            "project": entry.get("project", ""),
            "tier": entry.get("tier", ""),
            "preview": entry.get("preview", ""),
            "content_hash": h,
        })

    # Determine which entries need re-embedding
    existing_meta = {} if args.force else load_existing_meta()
    existing_embeddings = None if args.force else load_existing_embeddings()

    # Build path->index map for existing embeddings
    existing_path_to_idx = {}
    if existing_meta and existing_embeddings is not None:
        existing_meta_list = json.loads(META_FILE.read_text("utf-8")) if META_FILE.exists() else []
        for i, m in enumerate(existing_meta_list):
            existing_path_to_idx[m["path"]] = i

    needs_embedding = []
    reuse_map = {}  # new_idx -> old_idx
    for i, m in enumerate(meta):
        old = existing_meta.get(m["path"])
        old_idx = existing_path_to_idx.get(m["path"])
        if (
            old
            and old_idx is not None
            and existing_embeddings is not None
            and old_idx < len(existing_embeddings)
            and old.get("content_hash") == m["content_hash"]
        ):
            reuse_map[i] = old_idx
        else:
            needs_embedding.append(i)

    if not args.quiet:
        print(f"Total entries: {len(entries)}")
        print(f"Reusing: {len(reuse_map)}, Need embedding: {len(needs_embedding)}")

    # Load model and encode only what's needed
    if needs_embedding:
        if not args.quiet:
            print(f"Loading model {MODEL_NAME}...")
        # Suppress warnings
        import warnings
        warnings.filterwarnings("ignore")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(MODEL_NAME)

        texts_to_embed = [texts[i] for i in needs_embedding]
        if not args.quiet:
            print(f"Encoding {len(texts_to_embed)} texts...")
        new_embeddings = model.encode(texts_to_embed, show_progress_bar=not args.quiet, normalize_embeddings=True)
    else:
        new_embeddings = np.empty((0, 384), dtype=np.float32)
        if not args.quiet:
            print("All embeddings cached, no model load needed.")

    # Assemble final embedding matrix
    dim = 384
    final_embeddings = np.zeros((len(meta), dim), dtype=np.float32)

    # Place reused embeddings
    for new_idx, old_idx in reuse_map.items():
        final_embeddings[new_idx] = existing_embeddings[old_idx]

    # Place new embeddings
    for j, new_idx in enumerate(needs_embedding):
        final_embeddings[new_idx] = new_embeddings[j]

    # Save
    EMBEDDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(str(EMBEDDING_FILE), embeddings=final_embeddings)
    META_FILE.write_text(json.dumps(meta, indent=1) + "\n", "utf-8")

    elapsed = time.time() - t0
    size_kb = EMBEDDING_FILE.stat().st_size / 1024

    if not args.quiet:
        print(f"Done in {elapsed:.1f}s")
        print(f"Index: {EMBEDDING_FILE} ({size_kb:.0f} KB)")
        print(f"Meta: {META_FILE}")
        print(f"Entries: {len(meta)}, Dimensions: {dim}")


if __name__ == "__main__":
    main()
