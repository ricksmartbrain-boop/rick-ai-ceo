#!/usr/bin/env python3
"""Semantic retrieval layer for comm_history.

Uses openclaw infer embedding create (text-embedding-3-small, 1536-dim, local transport)
to build a per-recipient profile index backed by numpy cosine similarity.

ADDITIVE ONLY — never modifies comm_history exact-match paths.

Public API
----------
build_index(days_back=30) -> int
    Full rebuild of embedding index. Returns recipient count.

index_recipient(email) -> dict | None
    Embed and upsert a single recipient. Returns the index record.

find_similar(query_text, top_k=10) -> list[dict]
    Semantic search over all indexed recipients.
    Returns: [{email, similarity, last_touch_ts, touch_count, outcome_summary}, ...]

find_warmest_silent_leads(days_silent_min=14, top_k=10) -> list[dict]
    Silent leads ranked by converter-similarity × recency.
    Returns: [{email, score, similarity, days_silent, last_touch_ts, ...}, ...]

is_index_fresh(max_age_h=2.0) -> bool
    Health check: True if index was rebuilt within max_age_h hours.

Index file
----------
~/rick-vault/operations/comm-embeddings-index.jsonl
  Each line: {email, embedded_at, profile_text, embedding:[float*1536],
              last_touch_ts, touch_count, outcome_summary}
  Trailing line: {record_type:"index_meta", status:"ok", ts, count, model}
  → flag_health probes for the trailing meta record (RICK_EMBEDDING_INDEX_LIVE, 2h)

Model
-----
openclaw infer embedding create → openai/text-embedding-3-small via local transport
Dimensions: 1536  Cost: ~$0.00002/1K tokens (~free at this scale)
Token limit: 8191 tokens per text (profile text ~100–400 tokens)

Refresh cadence
---------------
Intended to run hourly via LaunchAgent or cron. build_index() is idempotent.
Partial failures (batch embed errors) are logged and skipped — index stays live.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
OPS = DATA_ROOT / "operations"
INDEX_FILE = OPS / "comm-embeddings-index.jsonl"
INDEX_REFRESH_H: int = 2       # flag_health stale threshold (hours)
SOURCE_DAYS: int = 30          # look-back window for build_index
EMBED_DIM: int = 1536          # text-embedding-3-small dimensionality
BATCH_SIZE: int = 20           # recipients per openclaw infer call
EMBED_MODEL: str = "openai/text-embedding-3-small"

# Seed query for find_warmest_silent_leads — what a "warm converter" looks like
_CONVERTER_SEED = (
    "B2B SaaS founder or startup CEO who replied positively, expressed interest "
    "in AI CEO tools, asked about pricing or a demo, mentioned a real business "
    "problem we could solve together. Warm lead, engaged, replied to outreach, "
    "building developer tools, Series A or B stage company."
)

# ---------------------------------------------------------------------------
# Embedding CLI wrapper
# ---------------------------------------------------------------------------

def _embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed multiple texts in one openclaw infer embedding create call.

    Raises RuntimeError on subprocess failure or malformed JSON.
    """
    if not texts:
        return []

    cmd = ["openclaw", "infer", "embedding", "create", "--json"]
    for t in texts:
        cmd += ["--text", t]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"openclaw infer embedding timed out ({len(texts)} texts)") from exc

    if result.returncode != 0:
        raise RuntimeError(
            f"openclaw infer embedding failed rc={result.returncode}: "
            f"{result.stderr[:300]}"
        )

    # Strip config-warning preamble (lines before first '{')
    stdout = result.stdout
    brace_pos = stdout.find("{")
    if brace_pos > 0:
        stdout = stdout[brace_pos:]

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"openclaw infer embedding bad JSON: {exc}: {stdout[:300]}"
        ) from exc

    if not data.get("ok"):
        raise RuntimeError(f"openclaw infer embedding not ok: {data}")

    outputs = data.get("outputs", [])
    if len(outputs) != len(texts):
        raise RuntimeError(
            f"Expected {len(texts)} embeddings, got {len(outputs)}"
        )

    return [o["embedding"] for o in outputs]


def _embed_single(text: str) -> list[float]:
    return _embed_batch([text])[0]


# ---------------------------------------------------------------------------
# Profile text builder — converts touch history → embeddable string
# ---------------------------------------------------------------------------

def _build_profile_text(email: str, touches: list[dict]) -> str:
    """Summarise a recipient's entire touch history into ~100-400 token text."""
    if not touches:
        return f"Recipient: {email}. No communication history."

    statuses: dict[str, int] = {}
    modalities: set[str] = set()
    subjects: list[str] = []
    excerpts: list[str] = []

    for t in touches:
        st = (t.get("status") or "").strip()
        if st:
            statuses[st] = statuses.get(st, 0) + 1
        mod = (t.get("modality") or "").strip()
        if mod:
            modalities.add(mod)
        subj = (t.get("subject") or "").strip()
        if subj and subj not in subjects:
            subjects.append(subj)
        exc = (t.get("body_excerpt") or "").strip()
        if exc and len(exc) > 20 and exc not in excerpts:
            excerpts.append(exc[:120])

    last_touch = touches[-1]
    last_ts = (last_touch.get("ts") or "")[:10]
    outcome = ", ".join(
        f"{c} {s}" for s, c in sorted(statuses.items(), key=lambda x: -x[1])
    )
    channels = ", ".join(sorted(modalities))

    parts = [
        f"Recipient: {email}.",
        f"Total touches: {len(touches)}.",
        f"Last contact: {last_ts}.",
        f"Channels: {channels}.",
        f"Outcomes: {outcome}.",
    ]
    if subjects:
        parts.append(f"Email subjects: {'; '.join(subjects[:6])}.")
    if excerpts:
        parts.append(f"Message excerpts: {' | '.join(excerpts[:3])}.")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Datetime helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip().rstrip("Z")
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f+00:00",
        "%Y-%m-%dT%H:%M:%S+00:00",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Index I/O
# ---------------------------------------------------------------------------

def _load_index() -> dict[str, dict]:
    """Load index JSONL → dict[email, record]. Skips meta records."""
    if not INDEX_FILE.exists():
        return {}
    records: dict[str, dict] = {}
    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("record_type") == "index_meta":
                    continue
                email = rec.get("email", "")
                if email:
                    records[email] = rec
    except OSError:
        return {}
    return records


def _save_index(records: dict[str, dict], count: int) -> None:
    """Atomically overwrite index JSONL. Trailing meta record for flag_health."""
    OPS.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_FILE.with_suffix(".tmp")
    now_str = _now_utc().isoformat(timespec="seconds")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            for rec in records.values():
                fh.write(json.dumps(rec, default=float) + "\n")
            # flag_health probes this trailing record
            meta = {
                "record_type": "index_meta",
                "status": "ok",
                "ts": now_str,
                "count": count,
                "model": EMBED_MODEL,
            }
            fh.write(json.dumps(meta) + "\n")
        tmp.replace(INDEX_FILE)
    except OSError as exc:
        _log_error(f"_save_index write failed: {exc}")
        raise RuntimeError(f"_save_index failed: {exc}") from exc


def _read_last_meta() -> dict | None:
    """Read trailing index_meta record from end of index JSONL."""
    if not INDEX_FILE.exists():
        return None
    try:
        with open(INDEX_FILE, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 8192))
            tail = fh.read().decode("utf-8", errors="replace")
        for line in reversed(tail.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("record_type") == "index_meta":
                    return rec
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return None


# ---------------------------------------------------------------------------
# Cosine similarity (numpy, no FAISS dependency)
# ---------------------------------------------------------------------------

def _cosine(a: list[float], b: list[float]) -> float:
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    norm_a = float(np.linalg.norm(va))
    norm_b = float(np.linalg.norm(vb))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (norm_a * norm_b))


def _outcome_summary(statuses: list[str]) -> str:
    counts: dict[str, int] = {}
    for s in statuses:
        s = s.strip()
        if s:
            counts[s] = counts.get(s, 0) + 1
    if not counts:
        return ""
    return ", ".join(f"{c} {s}" for s, c in sorted(counts.items(), key=lambda x: -x[1]))


# ---------------------------------------------------------------------------
# Public: build_index
# ---------------------------------------------------------------------------

def build_index(days_back: int = SOURCE_DAYS) -> int:
    """Rebuild embedding index for all recipients active in last `days_back` days.

    Batches embedding calls (BATCH_SIZE at a time) to minimise subprocess overhead.
    Preserves existing index records for recipients outside the window.
    Returns number of recipients in final index.
    """
    try:
        from runtime.comm_history import aggregate_by_recipient  # type: ignore
    except ImportError:
        from comm_history import aggregate_by_recipient  # type: ignore

    all_touches: dict[str, list[dict]] = aggregate_by_recipient(days_back=days_back)
    if not all_touches:
        _log_error("build_index: aggregate_by_recipient returned 0 recipients")
        return 0

    # Carry forward existing records (preserves out-of-window recipients)
    records: dict[str, dict] = _load_index()

    recipients = list(all_touches.keys())
    profile_texts: dict[str, str] = {
        e: _build_profile_text(e, t) for e, t in all_touches.items()
    }
    now_str = _now_utc().isoformat(timespec="seconds")

    indexed = 0
    for i in range(0, len(recipients), BATCH_SIZE):
        batch = recipients[i : i + BATCH_SIZE]
        texts = [profile_texts[e] for e in batch]

        try:
            embeddings = _embed_batch(texts)
        except RuntimeError as exc:
            _log_error(f"build_index batch {i}–{i + len(batch)} embed failed: {exc}")
            continue

        for email, text, emb in zip(batch, texts, embeddings):
            touches = all_touches[email]
            last_touch = touches[-1] if touches else {}
            records[email] = {
                "email": email,
                "embedded_at": now_str,
                "profile_text": text,
                "embedding": emb,
                "last_touch_ts": (last_touch.get("ts") or "")[:19],
                "touch_count": len(touches),
                "outcome_summary": _outcome_summary(
                    [t.get("status", "") for t in touches]
                ),
            }
            indexed += 1

    _save_index(records, len(records))
    return len(records)


# ---------------------------------------------------------------------------
# Public: index_recipient
# ---------------------------------------------------------------------------

def index_recipient(email: str) -> dict | None:
    """Embed and upsert a single recipient into the index.

    Returns the new index record, or None on embed failure.
    """
    try:
        from runtime.comm_history import get_history  # type: ignore
    except ImportError:
        from comm_history import get_history  # type: ignore

    email = email.strip().lower()
    if not email:
        return None

    touches = get_history(email)
    profile_text = _build_profile_text(email, touches)

    try:
        emb = _embed_single(profile_text)
    except RuntimeError as exc:
        _log_error(f"index_recipient {email} embed failed: {exc}")
        return None

    last_touch = touches[-1] if touches else {}
    record: dict[str, Any] = {
        "email": email,
        "embedded_at": _now_utc().isoformat(timespec="seconds"),
        "profile_text": profile_text,
        "embedding": emb,
        "last_touch_ts": (last_touch.get("ts") or "")[:19],
        "touch_count": len(touches),
        "outcome_summary": _outcome_summary([t.get("status", "") for t in touches]),
    }

    existing = _load_index()
    existing[email] = record
    _save_index(existing, len(existing))
    return record


# ---------------------------------------------------------------------------
# Public: find_similar
# ---------------------------------------------------------------------------

def find_similar(query_text: str, top_k: int = 10) -> list[dict]:
    """Semantic search: top_k recipients most similar to query_text.

    Returns list of dicts sorted by similarity descending:
    [{email, similarity, last_touch_ts, touch_count, outcome_summary, profile_text}]
    """
    if not query_text or not query_text.strip():
        return []

    try:
        query_emb = _embed_single(query_text.strip())
    except RuntimeError as exc:
        _log_error(f"find_similar embed failed: {exc}")
        return []

    index = _load_index()
    if not index:
        return []

    scored: list[dict] = []
    for email, rec in index.items():
        emb = rec.get("embedding")
        if not emb or len(emb) != EMBED_DIM:
            continue
        sim = _cosine(query_emb, emb)
        scored.append({
            "email": email,
            "similarity": round(sim, 4),
            "last_touch_ts": rec.get("last_touch_ts", ""),
            "touch_count": rec.get("touch_count", 0),
            "outcome_summary": rec.get("outcome_summary", ""),
            "profile_text": rec.get("profile_text", ""),
        })

    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:top_k]


# ---------------------------------------------------------------------------
# Public: find_warmest_silent_leads
# ---------------------------------------------------------------------------

def find_warmest_silent_leads(
    days_silent_min: int = 14,
    top_k: int = 10,
) -> list[dict]:
    """Return leads silent >= days_silent_min days, ranked by converter-similarity.

    Score formula:
        score = cosine_similarity(recipient_embedding, CONVERTER_SEED) * recency_weight
        recency_weight = 1 / (1 + days_silent / 30)

    Rationale: higher similarity to a "warm converter" profile × penalty for being
    too stale → surfaces the leads most likely to respond if nudged now.

    Returns list sorted by score descending:
    [{email, score, similarity, days_silent, last_touch_ts, touch_count, outcome_summary}]
    """
    now = _now_utc()
    cutoff = now - timedelta(days=days_silent_min)

    try:
        seed_emb = _embed_single(_CONVERTER_SEED)
    except RuntimeError as exc:
        _log_error(f"find_warmest_silent_leads seed embed failed: {exc}")
        return []

    index = _load_index()
    if not index:
        return []

    results: list[dict] = []
    for email, rec in index.items():
        last_ts = _parse_dt(rec.get("last_touch_ts"))
        if last_ts is None:
            continue
        if last_ts > cutoff:
            # Active within the silent window — skip
            continue

        emb = rec.get("embedding")
        if not emb or len(emb) != EMBED_DIM:
            continue

        days_silent = max(0.0, (now - last_ts).total_seconds() / 86400.0)
        sim = _cosine(seed_emb, emb)
        recency_weight = 1.0 / (1.0 + days_silent / 30.0)
        score = sim * recency_weight

        results.append({
            "email": email,
            "score": round(score, 4),
            "similarity": round(sim, 4),
            "days_silent": round(days_silent, 1),
            "last_touch_ts": rec.get("last_touch_ts", ""),
            "touch_count": rec.get("touch_count", 0),
            "outcome_summary": rec.get("outcome_summary", ""),
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]


# ---------------------------------------------------------------------------
# Public: is_index_fresh
# ---------------------------------------------------------------------------

def is_index_fresh(max_age_h: float = float(INDEX_REFRESH_H)) -> bool:
    """True if the index was rebuilt within max_age_h hours."""
    if not INDEX_FILE.exists():
        return False

    meta = _read_last_meta()
    if meta is not None:
        ts = _parse_dt(meta.get("ts"))
        if ts is not None:
            age_h = (_now_utc() - ts).total_seconds() / 3600.0
            return age_h <= max_age_h

    # Fallback: file mtime
    try:
        mtime = INDEX_FILE.stat().st_mtime
        age_h = (_now_utc().timestamp() - mtime) / 3600.0
        return age_h <= max_age_h
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _log_error(msg: str) -> None:
    log_path = OPS / "comm-embeddings-errors.jsonl"
    try:
        OPS.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": _now_utc().isoformat(), "error": msg}) + "\n")
    except OSError:
        pass
    print(f"[comm_embeddings] ERROR: {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="comm_embeddings — semantic retrieval CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("build", help="Build/rebuild embedding index (last 30d touches)")

    qp = sub.add_parser("query", help="Semantic search over index")
    qp.add_argument("text", help="Query string")
    qp.add_argument("--top-k", type=int, default=5)

    wp = sub.add_parser("warmest", help="Find warmest silent leads")
    wp.add_argument("--days-silent", type=int, default=14)
    wp.add_argument("--top-k", type=int, default=5)

    sp = sub.add_parser("status", help="Index freshness status")

    ip = sub.add_parser("index-one", help="Embed and upsert one recipient")
    ip.add_argument("email")

    args = parser.parse_args()

    if args.cmd == "build":
        print(f"Building embedding index (last {SOURCE_DAYS}d)…")
        n = build_index()
        print(f"Done — {n} recipients indexed → {INDEX_FILE}")

    elif args.cmd == "query":
        results = find_similar(args.text, top_k=args.top_k)
        print(f"\nTop {args.top_k} for: {args.text!r}\n")
        print(f"{'SIM':>6}  {'OUTCOMES':<28}  EMAIL")
        print("-" * 72)
        for r in results:
            print(f"{r['similarity']:>6.4f}  {r['outcome_summary']:<28}  {r['email']}")

    elif args.cmd == "warmest":
        results = find_warmest_silent_leads(
            days_silent_min=args.days_silent, top_k=args.top_k
        )
        print(f"\nWarmest silent leads (≥{args.days_silent}d silent):\n")
        print(f"{'SCORE':>6}  {'SIM':>6}  {'SILENT':>7}  {'OUTCOMES':<20}  EMAIL")
        print("-" * 80)
        for r in results:
            print(
                f"{r['score']:>6.4f}  {r['similarity']:>6.4f}  "
                f"{r['days_silent']:>5.0f}d  {r['outcome_summary']:<20}  {r['email']}"
            )

    elif args.cmd == "status":
        fresh = is_index_fresh()
        meta = _read_last_meta()
        if meta:
            print(
                f"Index: {'FRESH ✓' if fresh else 'STALE ✗'} | "
                f"built={meta.get('ts')} | count={meta.get('count')} | "
                f"model={meta.get('model')}"
            )
        else:
            print("Index: NOT BUILT — run: python3 runtime/comm_embeddings.py build")

    elif args.cmd == "index-one":
        rec = index_recipient(args.email)
        if rec:
            print(f"Indexed {args.email}: {rec.get('touch_count')} touches, {rec.get('outcome_summary')}")
        else:
            print(f"Failed to index {args.email}")

    else:
        parser.print_help()
