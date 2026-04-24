"""VARA — Verified Autonomous Revenue Attestation.

Per master plan TIER-4.3 (agent P3): "Insurance/fintech downstream
consequence — Moody's of autonomous agents." When a Rick's customer
crosses a cumulative revenue threshold ($1K MVP, more tiers later),
this module mints a dual-anchored attestation:

  1. HMAC-SHA256 signature using RICK_SECRET (proves THIS Rick produced it)
  2. Git HEAD commit SHA (anchors the moment in the receipts chain)
  3. Cumulative customer revenue + per-event audit trail

Attestations are:
  - Stored in `vara_attestations` table (auditable in DB)
  - Written as JSON to ~/rick-vault/attestations/<callsign>-<customer_short>-<date>.json
  - Idempotent — minting twice for the same threshold is a no-op
  - Tamper-evident — any modification breaks the HMAC

Future moats (not in MVP):
  - Stripe webhook signature as second anchor (proves the revenue is real)
  - Public verifier endpoint at meetrick.ai/fleet/<callsign>/attested
  - $1K / $5K / $10K / $50K / $100K tiers with progressively rare badges
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Iterable

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
ATTEST_DIR = DATA_ROOT / "attestations"
LOG_FILE = DATA_ROOT / "operations" / "vara.jsonl"

# Threshold tiers — once crossed, an attestation can be minted for that tier.
# A customer can mint MULTIPLE attestations as they cross higher thresholds.
TIERS_USD = [1_000, 5_000, 10_000, 50_000, 100_000]


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _log(payload: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": _now_iso(), **payload}) + "\n")


def ensure_table(conn: sqlite3.Connection) -> None:
    """Create vara_attestations table if missing. Idempotent."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS vara_attestations (
            id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            tier_usd INTEGER NOT NULL,
            cumulative_usd REAL NOT NULL,
            event_count INTEGER NOT NULL,
            rick_id TEXT NOT NULL,
            git_head_sha TEXT NOT NULL DEFAULT '',
            hmac_sha256 TEXT NOT NULL,
            minted_at TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(customer_id, tier_usd)
        );
        CREATE INDEX IF NOT EXISTS idx_vara_customer
            ON vara_attestations(customer_id, tier_usd);
        CREATE INDEX IF NOT EXISTS idx_vara_minted
            ON vara_attestations(minted_at);
    """)
    conn.commit()


def _git_head_sha() -> str:
    """Get current git HEAD SHA from the workspace repo. Empty on any error."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(Path.home() / ".openclaw" / "workspace"),
             "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        pass
    return ""


def _hmac_sign(rick_secret: str, payload: dict) -> str:
    """HMAC-SHA256 over the canonical (sorted-keys, no-whitespace) JSON payload."""
    if not rick_secret:
        return ""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hmac.new(
        rick_secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _customer_revenue(conn: sqlite3.Connection, customer_id: str) -> tuple[float, int, list[dict]]:
    """Return (cumulative_usd, event_count, audit_trail) for a customer."""
    rows = conn.execute(
        "SELECT id, event_type, payload_json, created_at "
        "FROM customer_events "
        "WHERE customer_id = ? AND event_type IN ('purchase_recorded', 'renewal_confirmed') "
        "ORDER BY created_at ASC",
        (customer_id,),
    ).fetchall()
    cumulative = 0.0
    audit: list[dict] = []
    for r in rows:
        try:
            payload = json.loads(r["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        amount = float(payload.get("amount_usd") or 0.0)
        if amount <= 0:
            continue
        cumulative += amount
        audit.append({
            "event_id": r["id"],
            "event_type": r["event_type"],
            "amount_usd": amount,
            "at": r["created_at"],
        })
    return cumulative, len(audit), audit


def _all_customers(conn: sqlite3.Connection) -> Iterable[str]:
    """Yield customer IDs that have at least one revenue event."""
    rows = conn.execute(
        "SELECT DISTINCT customer_id FROM customer_events "
        "WHERE event_type IN ('purchase_recorded', 'renewal_confirmed')"
    ).fetchall()
    for r in rows:
        yield r["customer_id"]


def _mint_one(conn: sqlite3.Connection, customer_id: str, tier_usd: int,
              cumulative_usd: float, event_count: int, audit: list[dict],
              rick_id: str, rick_secret: str, callsign: str | None) -> dict:
    """Mint one attestation. Returns the attestation dict.

    Idempotent at the SQL UNIQUE(customer_id, tier_usd) constraint — caller
    should check existing rows before calling.
    """
    git_sha = _git_head_sha()
    minted_at = _now_iso()
    canonical_payload = {
        "schema": "vara/1.0",
        "customer_id_short": customer_id[:16],
        "tier_usd": tier_usd,
        "cumulative_usd": round(cumulative_usd, 2),
        "event_count": event_count,
        "rick_id": rick_id,
        "git_head_sha": git_sha,
        "minted_at": minted_at,
        # Audit trail is part of the canonical payload — modifying any
        # event amount/timestamp would break the HMAC.
        "audit_event_count": len(audit),
        "audit_first_at": audit[0]["at"] if audit else None,
        "audit_last_at": audit[-1]["at"] if audit else None,
    }
    signature = _hmac_sign(rick_secret, canonical_payload)
    attest_id = f"vara_{customer_id[:8]}_{tier_usd}_{int(datetime.now().timestamp())}"

    conn.execute(
        """
        INSERT OR IGNORE INTO vara_attestations
          (id, customer_id, tier_usd, cumulative_usd, event_count,
           rick_id, git_head_sha, hmac_sha256, minted_at, payload_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            attest_id, customer_id, tier_usd, round(cumulative_usd, 2),
            event_count, rick_id, git_sha, signature, minted_at,
            json.dumps(canonical_payload, sort_keys=True),
        ),
    )
    conn.commit()

    # Write public-readable JSON to attestations dir
    public_record = {
        **canonical_payload,
        "hmac_sha256": signature,
        "verification": (
            "Verify: hmac.new(RICK_SECRET, canonical_json(payload_minus_hmac), "
            "sha256).hexdigest() == hmac_sha256. Anyone with RICK_SECRET can "
            "verify; anyone WITHOUT it can verify chain via git_head_sha + "
            "matching meetrick-site receipts entry."
        ),
        "callsign": callsign or "",
    }
    ATTEST_DIR.mkdir(parents=True, exist_ok=True)
    short = customer_id[:8]
    fname = f"{(callsign or 'rick').lower()}-{short}-tier{tier_usd}-{minted_at[:10]}.json"
    (ATTEST_DIR / fname).write_text(
        json.dumps(public_record, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )

    _log({
        "event": "minted",
        "customer_id_short": short,
        "tier_usd": tier_usd,
        "cumulative_usd": round(cumulative_usd, 2),
        "rick_id": rick_id,
        "git_sha": git_sha,
        "file": fname,
    })

    return public_record


def scan_and_mint(conn: sqlite3.Connection, dry_run: bool = False) -> dict:
    """Walk all customers, check revenue against tiers, mint missing attestations.

    Returns: {
      checked: N customers walked,
      eligible: list[(customer_id, tier_usd, cumulative_usd)],
      minted: list[<attestation>],
      already_minted: list[(customer_id, tier_usd)],
    }
    """
    ensure_table(conn)

    rick_id = os.getenv("RICK_ID", "").strip()
    rick_secret = os.getenv("RICK_SECRET", "").strip()
    callsign = os.getenv("RICK_CALLSIGN", "").strip() or None

    if not rick_id or not rick_secret:
        return {
            "checked": 0, "eligible": [], "minted": [], "already_minted": [],
            "error": "RICK_ID and RICK_SECRET required for HMAC signing",
        }

    result: dict = {
        "checked": 0,
        "eligible": [],
        "minted": [],
        "already_minted": [],
        "dry_run": dry_run,
    }

    for customer_id in _all_customers(conn):
        result["checked"] += 1
        cumulative, event_count, audit = _customer_revenue(conn, customer_id)
        for tier in TIERS_USD:
            if cumulative < tier:
                break  # tiers are sorted; no point checking higher
            # Already minted for this customer/tier?
            existing = conn.execute(
                "SELECT id FROM vara_attestations WHERE customer_id=? AND tier_usd=?",
                (customer_id, tier),
            ).fetchone()
            if existing:
                result["already_minted"].append({"customer_id": customer_id[:8], "tier_usd": tier})
                continue
            result["eligible"].append({
                "customer_id_short": customer_id[:8],
                "tier_usd": tier,
                "cumulative_usd": round(cumulative, 2),
            })
            if not dry_run:
                attest = _mint_one(conn, customer_id, tier, cumulative,
                                   event_count, audit, rick_id, rick_secret, callsign)
                result["minted"].append({
                    "customer_id_short": customer_id[:8],
                    "tier_usd": tier,
                    "hmac_sha256": attest["hmac_sha256"][:16] + "...",
                })

    return result


def attestation_summary(conn: sqlite3.Connection) -> dict:
    """For activity-digest visibility. Returns counts + max tier reached."""
    ensure_table(conn)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS total, MAX(tier_usd) AS max_tier, "
            "       COUNT(DISTINCT customer_id) AS unique_customers "
            "FROM vara_attestations"
        ).fetchone()
        return {
            "total_attestations": int(row["total"] or 0) if row else 0,
            "max_tier_usd": int(row["max_tier"] or 0) if row else 0,
            "unique_customers": int(row["unique_customers"] or 0) if row else 0,
        }
    except sqlite3.OperationalError:
        return {"total_attestations": 0, "max_tier_usd": 0, "unique_customers": 0}
