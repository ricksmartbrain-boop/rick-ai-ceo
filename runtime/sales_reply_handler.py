"""sales_reply_handler.py — Detect new sales_inquiry / pricing inbounds in reply-router.jsonl
and auto-draft a Gmail response. Never auto-sends. Surfaces to Vlad via Telegram.

Called from the heartbeat. Idempotent: tracks handled entries by (file, email, ran_at) hash.

Usage:
    python3 -m runtime.sales_reply_handler          # check + draft
    python3 -m runtime.sales_reply_handler --dry-run  # preview only
    python3 -m runtime.sales_reply_handler --status   # show pending drafts
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── Env load ──────────────────────────────────────────────────────────────────
for _env_file in [
    Path.home() / "clawd" / "config" / "rick.env",
    Path.home() / ".openclaw" / "workspace" / "config" / "rick.env",
]:
    if _env_file.exists():
        for _line in _env_file.read_text().splitlines():
            _line = _line.strip()
            if _line.startswith("export "):
                _line = _line[7:]
            if "=" in _line and not _line.startswith("#"):
                k, _, v = _line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        break

# ── Paths ─────────────────────────────────────────────────────────────────────
DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
REPLY_ROUTER = DATA_ROOT / "operations" / "reply-router.jsonl"
DRAFT_DIR = DATA_ROOT / "mailbox" / "drafts" / "sales"
STATE_FILE = DATA_ROOT / "control" / "sales-reply-handler-state.json"
LOG_FILE = DATA_ROOT / "operations" / "auto-draft-reply.jsonl"

# How far back to scan (avoid re-processing ancient entries)
SCAN_WINDOW_HOURS = 48
TARGET_LABELS = {"sales_inquiry", "pricing", "pricing_question"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds").replace("+00:00", "Z")


def _entry_id(row: dict) -> str:
    key = f"{row.get('ran_at','')}-{row.get('email','')}-{row.get('label','')}"
    return hashlib.md5(key.encode()).hexdigest()[:16]


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"handled_ids": []}
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"handled_ids": []}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Keep only the last 500 handled IDs
    if len(state.get("handled_ids", [])) > 500:
        state["handled_ids"] = state["handled_ids"][-500:]
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _log(event: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a") as f:
        f.write(json.dumps({"ts": _now_iso(), **event}) + "\n")


# ── Draft generation ──────────────────────────────────────────────────────────

def _generate_draft(row: dict) -> dict:
    """Generate a draft response using Claude (opus-4-8). No auto-send."""
    import anthropic

    email = row.get("email", "")
    body = row.get("body", "")
    subject = row.get("subject", "(no subject)")
    label = row.get("label", "")

    name_guess = email.split("@")[0].split(".")[0].capitalize() if email else "there"

    system_prompt = (
        "You are Rick, the AI CEO at meetrick.ai. A potential customer has replied to one of "
        "your emails or reached out with a sales/pricing inquiry. Draft a warm, direct response "
        "in Vlad's voice (Vlad is the human founder; Rick is the AI agent). The tone is "
        "founder-to-founder: honest, no fluff, specific to their message. Do NOT auto-sell. "
        "Ask one clarifying question if the inquiry is vague. Reference the free 1-week pilot "
        "(meetrick.ai/pilot) as the suggested next step. Keep it under 150 words. "
        "Return ONLY the email body (no subject, no 'Hi', no sign-off — those will be added)."
    )

    user_prompt = (
        f"Inbound from {email} (label={label}).\n"
        f"Subject: {subject}\n"
        f"Body:\n{body[:800]}\n\n"
        "Draft a reply. 150 words max. One clear next step."
    )

    try:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        msg = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=300,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        draft_body = msg.content[0].text.strip()
    except Exception as exc:
        draft_body = (
            f"[DRAFT GENERATION FAILED: {exc}]\n\n"
            f"Manual draft needed for: {email}\n"
            f"Their message: {body[:200]}"
        )

    draft_subject = f"Re: {subject}" if not subject.startswith("Re:") else subject

    full_body = (
        f"Hi {name_guess},\n\n"
        f"{draft_body}\n\n"
        f"— Vlad\n"
        f"(building meetrick.ai — meetrick.ai/pilot for the free week)"
    )

    return {
        "to": email,
        "subject": draft_subject,
        "body": full_body,
        "original_label": label,
        "original_subject": subject,
        "original_body": body[:400],
        "auto_send": False,
        "review_required": True,
        "model": "claude-opus-4-8",
        "created_at": _now_iso(),
    }


def _save_draft(entry_id: str, draft: dict) -> Path:
    DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    path = DRAFT_DIR / f"{entry_id}-sales-draft.json"
    path.write_text(json.dumps(draft, indent=2))
    return path


def _notify_vlad(draft: dict, draft_path: Path) -> None:
    """Send a Telegram notification to ops-alerts."""
    try:
        import subprocess
        tg_script = ROOT / "scripts" / "tg-topic.sh"
        if not tg_script.exists():
            return
        to = draft.get("to", "?")
        subject = draft.get("subject", "?")
        snippet = draft.get("body", "")[:120].replace("\n", " ")
        msg = (
            f"Sales inquiry draft ready — {to}\n"
            f"Subject: {subject}\n"
            f"Draft: {snippet}...\n\n"
            f"File: {draft_path}\n"
            f"NEVER auto-send. Review + send manually."
        )
        subprocess.run(
            ["bash", str(tg_script), "ops-alerts", msg],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass


# ── Main scan ─────────────────────────────────────────────────────────────────

def scan_and_draft(dry_run: bool = False) -> int:
    """Scan reply-router.jsonl for new sales_inquiry/pricing entries. Draft + notify."""
    if not REPLY_ROUTER.exists():
        return 0

    state = load_state()
    handled_ids = set(state.get("handled_ids", []))
    cutoff = _now() - timedelta(hours=SCAN_WINDOW_HOURS)
    drafted = 0

    for line in REPLY_ROUTER.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Only process target labels
        if row.get("label") not in TARGET_LABELS:
            continue

        # Only entries within the scan window
        try:
            ran_at_str = row.get("ran_at", "")
            ran_at = datetime.fromisoformat(ran_at_str.replace("Z", "+00:00"))
            if ran_at.tzinfo is None:
                ran_at = ran_at.replace(tzinfo=timezone.utc)
            if ran_at < cutoff:
                continue
        except Exception:
            continue

        # Skip if already handled
        entry_id = _entry_id(row)
        if entry_id in handled_ids:
            continue

        # Skip self-emails (rick@meetrick.ai, test entries)
        email = row.get("email", "")
        if "meetrick.ai" in email or "belkins.io" in email or "example" in email:
            handled_ids.add(entry_id)
            continue

        drafted += 1

        if dry_run:
            print(f"[DRY-RUN] would draft for: {email} label={row.get('label')} subject={row.get('subject','?')[:60]}")
            handled_ids.add(entry_id)
            continue

        # Generate draft
        draft = _generate_draft(row)
        draft_path = _save_draft(entry_id, draft)
        _notify_vlad(draft, draft_path)
        _log({
            "event": "draft_created",
            "entry_id": entry_id,
            "email": email,
            "label": row.get("label"),
            "draft_path": str(draft_path),
        })
        print(f"[sales_reply_handler] drafted for {email} → {draft_path.name}")
        handled_ids.add(entry_id)

    state["handled_ids"] = list(handled_ids)
    save_state(state)
    return drafted


def print_status() -> None:
    state = load_state()
    pending = list(DRAFT_DIR.glob("*-sales-draft.json")) if DRAFT_DIR.exists() else []
    print(f"Sales reply handler status:")
    print(f"  Handled IDs in state: {len(state.get('handled_ids', []))}")
    print(f"  Draft files pending review: {len(pending)}")
    for p in sorted(pending)[-5:]:
        try:
            d = json.loads(p.read_text())
            print(f"    {p.name}: to={d.get('to','')} subj={d.get('subject','')[:50]}")
        except Exception:
            print(f"    {p.name}: (unreadable)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.status:
        print_status()
        return

    n = scan_and_draft(dry_run=args.dry_run)
    if n == 0:
        print("[sales_reply_handler] no new sales inquiries")
    else:
        print(f"[sales_reply_handler] {n} draft(s) created")


if __name__ == "__main__":
    main()
