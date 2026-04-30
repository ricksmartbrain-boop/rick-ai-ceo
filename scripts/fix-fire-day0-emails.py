#!/usr/bin/env python3
"""
One-shot fix: fire Day-0 cold email for 3 stuck qualified_lead workflows.

Root cause: sequencer reads ctx.get('email') but lead data is stored under
ctx['trigger_payload']['email'] → "no email" skip on 2026-04-27.

Fix sequence per workflow:
  1. Promote trigger_payload fields to top-level ctx
  2. Remove stale skipped email-cold-1 entry from touch_log
  3. Generate opus-4-7 opener via generate_text('review')
  4. Verify model is opus-4-7 or gpt-5.4 (hard-fail on mini)
  5. Save vault JSON
  6. Write mailbox outbox file (email formatter pattern)
  7. Create outbound_job in DB
  8. Update touch_log with queued status + job_id
  9. Update workflow stage to 'cold-email-sent'
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

# ── Repo path ────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Load env
env_file = REPO_ROOT / "config" / "rick.env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
SUPPRESSION_FILE = DATA_ROOT / "mailbox" / "suppression.txt"
OUTBOX_DIR = DATA_ROOT / "mailbox" / "outbox" / "ad-hoc"
VAULT_LEADS_DIR = DATA_ROOT / "projects" / "qualified-leads"

# ── Targets ──────────────────────────────────────────────────────────────────
TARGETS = [
    {"wf_id": "wf_050fb1d53cb7", "email": "arjun@rtrvr.ai",         "name": "Arjun Patel",  "domain": "rtrvr.ai"},
    {"wf_id": "wf_59ea6636384e", "email": "riley@charlielabs.ai",    "name": "Riley",        "domain": "charlielabs.ai"},
    {"wf_id": "wf_8c40eea7ce2f", "email": "hello@octokraft.com",     "name": "Ciprian",      "domain": "octokraft.com"},
]

# Smart-model invariant: only these are acceptable for cold openers
APPROVED_MODELS = {"claude-opus-4-7", "gpt-5.4"}

FROM_EMAIL = os.getenv("MEETRICK_FROM_EMAIL", "Rick <rick@meetrick.ai>")

# ── Helpers ──────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_suppression() -> set[str]:
    if not SUPPRESSION_FILE.exists():
        return set()
    suppressed = set()
    for line in SUPPRESSION_FILE.read_text(encoding="utf-8").splitlines():
        addr = line.split("#")[0].strip().lower()
        if addr and "@" in addr:
            suppressed.add(addr)
    return suppressed


def check_suppression(email: str, suppressed: set[str]) -> None:
    if email.lower() in suppressed:
        raise RuntimeError(f"SUPPRESSED — {email} is in suppression.txt. Aborting.")


def generate_opener(lead: dict) -> tuple[str, str, str]:
    """Returns (subject, body, model_used). Raises on bad model."""
    from runtime.llm import generate_text
    name = lead["name"]
    email = lead["email"]
    domain = lead["domain"]

    prompt = (
        "TASK: Write a cold outreach email. Output only the email — no analysis, "
        "no review commentary, no caveats.\n\n"
        "You are Rick, AI CEO at meetrick.ai. Write a short, sharp cold outreach email "
        "for this B2B lead. Goal: get ONE reply.\n\n"
        f"Lead name: {name}\n"
        f"Email: {email}\n"
        f"Company domain: {domain}\n\n"
        "Output format (exactly this structure):\n"
        "SUBJECT: <subject line, max 8 words>\n"
        "BODY:\n"
        "<2-3 short paragraphs, plain text, no markdown, no em dashes>\n\n"
        "Rules:\n"
        "- Open with a specific observation about their business or market positioning\n"
        "- Reference Rick (meetrick.ai) and one concrete outcome or capability\n"
        "- CTA: single conversational question, not a pitch\n"
        "- Sign off: Rick\n"
        "- Do NOT include disclaimers, analysis, meta-commentary, or refusals\n"
        "Write the email now."
    )
    fallback_text = (
        f"SUBJECT: Quick question for {domain}\n"
        f"BODY:\nHi {name},\n\n"
        f"I've been watching what you're building at {domain}. "
        "The operators who wire AI into their growth loops early tend to compound fast.\n\n"
        "I'm Rick, AI CEO at meetrick.ai. We help founders like you run faster with AI "
        "that actually touches revenue, not just demos.\n\n"
        "One question: what's the part of your business where a faster feedback loop "
        "would move the needle most right now?\n\nRick"
    )

    result = generate_text("review", prompt, fallback_text)
    model_used = result.model.strip()

    # Normalize: strip provider prefix if present (e.g. "anthropic/claude-opus-4-7" → "claude-opus-4-7")
    model_short = model_used.split("/")[-1] if "/" in model_used else model_used

    # Hard invariant: NEVER mini for these openers
    if model_short not in APPROVED_MODELS:
        raise RuntimeError(
            f"SMART-MODEL INVARIANT VIOLATED — got '{model_used}' "
            f"(short='{model_short}') for {email}. "
            f"Only {APPROVED_MODELS} allowed. Aborting this lead."
        )

    content = result.content.strip()

    # Parse SUBJECT / BODY
    subject = f"Quick question for {domain}"
    body_lines: list[str] = []
    in_body = False
    for line in content.splitlines():
        if line.startswith("SUBJECT:"):
            subject = line[len("SUBJECT:"):].strip()
        elif line.startswith("BODY:"):
            in_body = True
        elif in_body:
            body_lines.append(line)

    body = "\n".join(body_lines).strip() or content

    return subject, body, model_used


def save_vault_json(lead: dict, subject: str, body: str, model_used: str) -> None:
    VAULT_LEADS_DIR.mkdir(parents=True, exist_ok=True)
    path = VAULT_LEADS_DIR / f"{lead['wf_id']}.json"
    data = {
        "workflow_id": lead["wf_id"],
        "lead_email": lead["email"],
        "lead_name": lead["name"],
        "domain": lead["domain"],
        "subject": subject,
        "body": body,
        "generated_at": now_iso(),
        "model_used": model_used,
        "touch_kind": "email-cold-1",
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  [vault] Saved {path.name}")


def write_outbox_file(lead: dict, subject: str, body: str) -> str:
    """Write draft .md to outbox. Returns file path string."""
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    slug_source = (lead["email"].lower() + subject.lower()).encode("utf-8")
    slug = hashlib.sha1(slug_source).hexdigest()[:8]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = OUTBOX_DIR / f"{stamp}-{slug}-step1.md"
    content = (
        "---\n"
        f"to: {lead['email']}\n"
        f"subject: {subject}\n"
        f"from: {FROM_EMAIL}\n"
        f"workflow_id: {lead['wf_id']}\n"
        f"touch_kind: email-cold-1\n"
        "---\n\n"
        f"{body}\n"
    )
    path.write_text(content, encoding="utf-8")
    print(f"  [outbox] Wrote {path.name}")
    return str(path)


def create_outbound_job(conn, lead: dict, subject: str, body: str, draft_path: str) -> str:
    """Insert outbound_job row. Returns job_id."""
    from runtime.db import connect as db_connect  # noqa — already imported via conn
    job_id = f"obj_{uuid.uuid4().hex[:12]}"
    payload = {
        "to": lead["email"],
        "subject": subject,
        "body_md": body,
        "from": FROM_EMAIL,
        "lane": "distribution-lane",
        "msg_id": f"seq-{lead['wf_id'][:8]}-email-cold-1",
        "draft_path": draft_path,
        "workflow_id": lead["wf_id"],
    }
    conn.execute(
        """
        INSERT INTO outbound_jobs
            (id, lead_id, channel, template_id, payload_json, status, scheduled_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            lead["wf_id"],
            "email",
            "email-cold-1",
            json.dumps(payload),
            "queued",
            now_iso(),
            now_iso(),
        ),
    )
    print(f"  [db] outbound_job created: {job_id} (status=queued, channel=email)")
    return job_id


def fix_workflow(conn, lead: dict, suppressed: set[str], results: list) -> None:
    wf_id = lead["wf_id"]
    email = lead["email"]
    print(f"\n{'='*60}")
    print(f"Processing {wf_id} — {email}")

    # 1. Suppression check
    check_suppression(email, suppressed)
    print(f"  [ok] Suppression check passed for {email}")

    # 2. Load workflow from DB
    row = conn.execute("SELECT * FROM workflows WHERE id=?", (wf_id,)).fetchone()
    if not row:
        raise RuntimeError(f"Workflow {wf_id} not found in DB")

    ctx = json.loads(row["context_json"] or "{}")
    seq = ctx.setdefault("seq", {})
    touch_log: list = seq.setdefault("touch_log", [])

    # 3. Promote trigger_payload fields to top-level
    tp = ctx.get("trigger_payload", {})
    ctx["email"] = tp.get("email") or lead["email"]
    ctx["name"] = tp.get("name") or lead["name"]
    ctx["company"] = tp.get("domain") or lead["domain"]
    ctx["domain"] = tp.get("domain") or lead["domain"]
    ctx["icp_score"] = tp.get("icp_score", 0.85)
    print(f"  [fix] Promoted trigger_payload → top-level ctx (email={ctx['email']})")

    # 4. Remove stale skipped email-cold-1 entry
    stale_entries = [e for e in touch_log if e.get("kind") == "email-cold-1"]
    if stale_entries:
        touch_log[:] = [e for e in touch_log if e.get("kind") != "email-cold-1"]
        print(f"  [fix] Cleared {len(stale_entries)} stale email-cold-1 entry from touch_log")

    # 5. Generate opener with model verification
    print(f"  [llm] Calling generate_text('review') for {email} ...")
    subject, body, model_used = generate_opener(lead)
    print(f"  [llm] model_used={model_used} ✓ APPROVED")
    print(f"  [llm] subject='{subject}'")
    print(f"  [llm] body preview: {body[:120].replace(chr(10), ' ')!r}...")

    # 6. Save vault JSON
    save_vault_json(lead, subject, body, model_used)

    # 7. Write outbox file
    draft_path = write_outbox_file(lead, subject, body)

    # 8. Create outbound_job
    job_id = create_outbound_job(conn, lead, subject, body, draft_path)

    # 9. Update touch_log with real dispatch record
    touch_entry = {
        "kind": "email-cold-1",
        "channel": "email",
        "status": "queued",
        "outbound_job_id": job_id,
        "draft_path": draft_path,
        "model_used": model_used,
        "sent_at": now_iso(),
        "subject": subject,
    }
    touch_log.append(touch_entry)
    seq["last_touch_at"] = now_iso()
    # Record sequence start time (real trigger = now, since Day-0 was skipped in error)
    seq["sequence_started_at"] = now_iso()

    # 10. Persist context_json
    conn.execute(
        "UPDATE workflows SET context_json=?, stage='cold-email-sent', updated_at=? WHERE id=?",
        (json.dumps(ctx), now_iso(), wf_id),
    )
    conn.commit()
    print(f"  [db] workflow stage → cold-email-sent")

    results.append({
        "wf_id": wf_id,
        "email": email,
        "outbound_job_id": job_id,
        "model_used": model_used,
        "subject": subject,
        "draft_path": draft_path,
        "stage": "cold-email-sent",
        "dispatcher_result": "queued — outbox file written, awaiting email-sequence-send.py drain",
    })


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    from runtime.db import connect
    conn = connect()
    conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))

    suppressed = load_suppression()
    print(f"Loaded {len(suppressed)} suppressed addresses.")

    results: list[dict] = []
    errors: list[dict] = []

    for lead in TARGETS:
        try:
            fix_workflow(conn, lead, suppressed, results)
        except Exception as exc:
            print(f"  [ERROR] {lead['wf_id']}: {exc}")
            errors.append({"wf_id": lead["wf_id"], "email": lead["email"], "error": str(exc)})

    conn.close()

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Dispatched: {len(results)}/3  Errors: {len(errors)}/3")
    print()
    for r in results:
        print(f"  ✅ {r['wf_id']} ({r['email']})")
        print(f"     outbound_job_id : {r['outbound_job_id']}")
        print(f"     model_used      : {r['model_used']}")
        print(f"     subject         : {r['subject']}")
        print(f"     stage           : {r['stage']}")
        print(f"     dispatcher      : {r['dispatcher_result']}")
        print()
    for e in errors:
        print(f"  ❌ {e['wf_id']} ({e['email']}): {e['error']}")
        print()

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
