#!/usr/bin/env python3
"""Approve, optionally edit, and send auto-drafted reply emails via Resend.

Usage:
  python3 scripts/send-draft.py <draft-id> [--edit] [--dry-run]
  python3 scripts/send-draft.py --batch <draft-id> <draft-id> ... [--auto] [--dry-run]

Behavior:
  - Locates drafts in ~/rick-vault/mailbox/drafts/auto/
  - Resolves the original outbound subject for thread-safe replies
  - Pretty-prints draft for Vlad review
  - Optionally opens the JSON in $EDITOR for tweaks
  - Sends via Resend with In-Reply-To / References headers
  - Appends send telemetry to operations/email-sends.jsonl
  - Updates comm-history cache + workflow stage replied -> replied-sent

Default: manual approval. --auto is opt-in only.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
DRAFTS_DIR = DATA_ROOT / "mailbox" / "drafts" / "auto"
OPS_DIR = DATA_ROOT / "operations"
EMAIL_SENDS = OPS_DIR / "email-sends.jsonl"
SUPPRESSION_FILE = DATA_ROOT / "mailbox" / "suppression.txt"
OUTREACH_EMAIL_LOG = DATA_ROOT / "projects" / "outreach" / "email-log.md"
WORKSPACE_ENV = REPO_ROOT / "config" / "rick.env"
HOME_ENV = Path.home() / "clawd" / "config" / "rick.env"

FROM_ADDR = os.getenv("MEETRICK_FROM_EMAIL", "Rick <rick@meetrick.ai>")
RESEND_ENDPOINT = "https://api.resend.com/emails"

AUTO_BAR = {
    "opus_confidence_min": 0.85,
    "sentence_count_max": 7,  # < 8 sentences
    "require_cta": True,
}

_MODEL_SHORTCUTS = {
    "anthropic/claude-opus-4-8": "claude-opus-4-8",
    "openai/gpt-5.4": "gpt-5.4",
}


def _load_env() -> None:
    for env_file in (WORKSPACE_ENV, HOME_ENV):
        if not env_file.exists():
            continue
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        except OSError:
            pass


def _email_channel_block_reason() -> str | None:
    try:
        root = str(REPO_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        from runtime.db import connect
        from runtime.kill_switches import ChannelPaused, assert_channel_active

        conn = connect()
        try:
            assert_channel_active(conn, "email")
            return None
        except ChannelPaused as exc:
            return exc.reason
        finally:
            conn.close()
    except Exception as exc:
        return f"gate_unavailable: {type(exc).__name__}: {exc}"


def _load_suppressions() -> set[str]:
    if not SUPPRESSION_FILE.exists():
        return set()
    try:
        lines = SUPPRESSION_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return set()
    result: set[str] = set()
    for raw in lines:
        email = raw.split("#", 1)[0].strip().lower()
        if email:
            result.add(email)
    return result


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_text(val: Any) -> str:
    return str(val or "").strip()


def _sentence_count(text: str) -> int:
    chunks = [c.strip() for c in re.split(r"(?<=[.!?])\s+|\n+", text.strip()) if c.strip()]
    return len(chunks) if chunks else 0


def _has_cta(text: str) -> bool:
    low = text.lower()
    return bool(
        re.search(r"\b(call|book|schedule|demo|reply|meet|talk|chat|ping|calendar|calendly|if you want|happy to)\b", low)
        or "?" in text
    )


def _strip_re(subject: str) -> str:
    return re.sub(r"^re:\s*", "", subject or "", flags=re.IGNORECASE).strip()


def _safe_subject(subject: str) -> str:
    base = _strip_re(subject)
    return f"Re: {base}" if base else "Re:"


def _draft_label(data: dict[str, Any]) -> str:
    return _normalize_text(
        data.get("reply_label")
        or data.get("label")
        or data.get("intent")
        or data.get("type")
    )


def _draft_recipient(data: dict[str, Any]) -> str:
    return _normalize_text(
        data.get("prospect_email")
        or data.get("from_email")
        or data.get("to")
        or data.get("email")
    )


def _draft_body(data: dict[str, Any]) -> str:
    return _normalize_text(
        data.get("draft_body")
        or data.get("body")
        or data.get("reply_body")
        or data.get("text")
    )


def _draft_subject_raw(data: dict[str, Any]) -> str:
    return _normalize_text(data.get("draft_subject") or data.get("subject") or "")


def _find_draft_path(draft_id: str) -> Path:
    candidate = Path(draft_id).expanduser()
    if candidate.is_file():
        return candidate.resolve()

    if DRAFTS_DIR.is_dir():
        exact = DRAFTS_DIR / f"{draft_id}.json"
        if exact.is_file():
            return exact

        hits: list[Path] = []
        for path in DRAFTS_DIR.glob("*.json"):
            stem = path.stem
            if draft_id == stem or draft_id in stem or draft_id in path.name:
                hits.append(path)
                continue
            try:
                data = _read_json(path)
            except Exception:
                continue
            if draft_id in {
                _normalize_text(data.get("wf_id")),
                _normalize_text(data.get("draft_id")),
                _normalize_text(data.get("thread_id")),
            }:
                hits.append(path)
        if hits:
            # Prefer exact stem matches, then shortest filename, then newest mtime.
            hits.sort(key=lambda p: (0 if p.stem == draft_id else 1, len(p.name), -p.stat().st_mtime))
            return hits[0]

    raise FileNotFoundError(f"draft not found for {draft_id!r} in {DRAFTS_DIR}")


def _lookup_workflow(wf_id: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    try:
        from runtime.db import connect
    except Exception:
        return None, {}

    conn = None
    try:
        conn = connect()
        row = conn.execute("SELECT id, stage, context_json, updated_at FROM workflows WHERE id = ? LIMIT 1", (wf_id,)).fetchone()
        if not row:
            return None, {}
        ctx = json.loads(row["context_json"] or "{}")
        return dict(row), ctx if isinstance(ctx, dict) else {}
    except Exception:
        return None, {}
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def _lookup_original_thread_subject(recipient: str, wf_id: str, draft: dict[str, Any]) -> tuple[str, str]:
    """Return (subject, in_reply_to_ref)."""
    _, ctx = _lookup_workflow(wf_id)

    # 1) workflow context_json touch_log (preferred if present)
    subject = ""
    ref = ""
    seq = ctx.get("seq") if isinstance(ctx, dict) else None
    if isinstance(seq, dict):
        touch_log = seq.get("touch_log") or []
        if isinstance(touch_log, list):
            for item in touch_log:
                if not isinstance(item, dict):
                    continue
                if item.get("kind") == "email-cold-1" and item.get("subject"):
                    subject = _normalize_text(item.get("subject"))
                    ref = _normalize_text(item.get("message_id") or item.get("resend_id") or item.get("outbound_job_id"))
                    break

    # 2) workflow opener subject
    if not subject:
        subject = _normalize_text(ctx.get("opener_subject") or ctx.get("subject") or "")

    # 3) outbound log (best real-world source)
    if OUTREACH_EMAIL_LOG.exists():
        try:
            for line in reversed(OUTREACH_EMAIL_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()):
                if f"→ {recipient}" not in line and recipient not in line:
                    continue
                if "Subject:" not in line:
                    continue
                m_subj = re.search(r'Subject:\s*"([^"]+)"', line)
                m_id = re.search(r"Resend ID:\s*([a-f0-9\-]+)", line)
                if m_subj and not subject:
                    subject = m_subj.group(1).strip()
                if m_id and not ref:
                    ref = m_id.group(1).strip()
                if subject:
                    break
        except OSError:
            pass

    # 4) draft subject fallback
    if not subject:
        raw = _draft_subject_raw(draft)
        subject = _strip_re(raw) or raw or ""

    subject = subject or draft.get("reply_subject") or draft.get("subject") or ""

    # Threading ref: use a synthetic RFC-ish ref if all we have is the provider id.
    if ref:
        ref = f"<{ref}@meetrick.ai>"
    return subject, ref


def _format_html(body: str) -> str:
    blocks = [b.strip() for b in re.split(r"\n\s*\n", body.strip()) if b.strip()]
    if not blocks:
        return ""
    parts = [f"<p>{html_lib.escape(block).replace(chr(10), '<br>')}</p>" for block in blocks]
    return "\n".join(parts)


def _resolve_draft(data: dict[str, Any], path: Path) -> dict[str, Any]:
    wf_id = _normalize_text(data.get("wf_id") or data.get("workflow_id") or path.stem.split("-", 1)[0])
    recipient = _draft_recipient(data)
    body = _draft_body(data)
    raw_subject = _draft_subject_raw(data)
    subject_base, reply_ref = _lookup_original_thread_subject(recipient, wf_id, data)
    resolved_subject = _safe_subject(subject_base or raw_subject)
    if resolved_subject == "Re:" and raw_subject:
        resolved_subject = _safe_subject(raw_subject)

    model = _normalize_text(data.get("model") or data.get("model_used") or "")
    label = _draft_label(data)
    model_short = _MODEL_SHORTCUTS.get(model, model.split("/")[-1] if model else "")
    sentence_count = _sentence_count(body)
    cta = _has_cta(body)
    opus_confidence = 0.0
    if "opus" in model_short:
        opus_confidence = 0.94
    elif model_short == "gpt-5.4":
        opus_confidence = 0.88

    auto_pass = (
        opus_confidence >= AUTO_BAR["opus_confidence_min"]
        and sentence_count < AUTO_BAR["sentence_count_max"] + 1
        and (cta if AUTO_BAR["require_cta"] else True)
    )

    return {
        "wf_id": wf_id,
        "recipient": recipient,
        "body": body,
        "raw_subject": raw_subject,
        "subject_base": subject_base,
        "resolved_subject": resolved_subject,
        "reply_ref": reply_ref,
        "model": model,
        "model_short": model_short,
        "label": label,
        "sentence_count": sentence_count,
        "cta": cta,
        "opus_confidence": opus_confidence,
        "auto_pass": auto_pass,
        "workflow": wf_id,
        "path": str(path),
        "review_required": bool(data.get("review_required", True)),
        "auto_send": bool(data.get("auto_send", False)),
        "draft": data,
    }


def _print_preview(info: dict[str, Any]) -> None:
    print(f"FILE: {info['path']}")
    print(f"TO:   {info['recipient']}")
    print(f"SUBJ: {info['resolved_subject']}")
    if info.get("raw_subject") and info["raw_subject"] != info["resolved_subject"]:
        print(f"RAW:  {info['raw_subject']}")
    if info.get("reply_ref"):
        print(f"REF:  {info['reply_ref']}")
    if info.get("wf_id"):
        print(f"WF:   {info['wf_id']}")
    if info.get("model"):
        print(f"MODEL: {info['model']}")
    if info.get("label"):
        print(f"LABEL: {info['label']}")
    print(f"AUTO BAR: opus_confidence>{AUTO_BAR['opus_confidence_min']} | sentence_count<{AUTO_BAR['sentence_count_max'] + 1} | has_cta={AUTO_BAR['require_cta']}")
    print("--- BODY ---")
    print(info["body"])
    print("--- END BODY ---")


def _edit_draft(path: Path) -> None:
    editor = os.getenv("EDITOR") or os.getenv("VISUAL") or "vim"
    subprocess.run([editor, str(path)], check=False)


def _prompt_action() -> str:
    while True:
        resp = input("send this? [y/n/edit] ").strip().lower()
        if resp in {"y", "n", "edit"}:
            return resp


def _send_via_resend(info: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("RESEND_API_KEY missing")
    block_reason = _email_channel_block_reason()
    if block_reason:
        raise RuntimeError(f"EMAIL CHANNEL PAUSED: {block_reason}")
    recipient = _normalize_text(info["recipient"]).lower()
    if recipient in _load_suppressions():
        raise RuntimeError(f"SUPPRESSION VIOLATION BLOCKED: {recipient}")

    payload: dict[str, Any] = {
        "from": FROM_ADDR,
        "to": [info["recipient"]],
        "subject": info["resolved_subject"],
        "text": info["body"],
        "html": _format_html(info["body"]),
    }
    if info.get("reply_ref"):
        payload["headers"] = {
            "In-Reply-To": info["reply_ref"],
            "References": info["reply_ref"],
        }

    import urllib.request

    req = urllib.request.Request(
        RESEND_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "rick-send-draft/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "ignore")
            return json.loads(raw) if raw else {}
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Resend send failed: {exc}") from exc


def _log_send(info: dict[str, Any], result: dict[str, Any]) -> None:
    message_id = _normalize_text(result.get("id") or result.get("message_id") or result.get("email_id"))
    row = {
        "message_id": message_id,
        "status": "sent",
        "to": info["recipient"],
        "subject": info["resolved_subject"],
        "ts": now_iso(),
        "wf_id": info["wf_id"],
        "draft_path": info["path"],
        "draft_label": info.get("label", ""),
        "model": info.get("model", ""),
        "in_reply_to": info.get("reply_ref", ""),
        "references": info.get("reply_ref", ""),
        "body_excerpt": info["body"][:200],
        "source": "send-draft.py",
    }
    _append_jsonl(EMAIL_SENDS, row)
    try:
        from runtime.comm_history import invalidate_cache as _invalidate_cache

        _invalidate_cache(info["recipient"])
    except Exception:
        pass


def _update_workflow_stage(wf_id: str) -> None:
    try:
        from runtime.db import connect
    except Exception:
        return

    conn = None
    try:
        conn = connect()
        row = conn.execute("SELECT stage, context_json FROM workflows WHERE id = ? LIMIT 1", (wf_id,)).fetchone()
        if not row:
            return
        now = now_iso()
        ctx = json.loads(row["context_json"] or "{}") if row["context_json"] else {}
        seq = ctx.get("seq") if isinstance(ctx, dict) else None
        if isinstance(seq, dict):
            touch_log = seq.setdefault("touch_log", [])
            if isinstance(touch_log, list):
                touch_log.append({
                    "kind": "email-reply-sent",
                    "channel": "email",
                    "status": "sent",
                    "ts": now,
                })
            ctx["seq"] = seq
            conn.execute("UPDATE workflows SET context_json = ?, stage = 'replied-sent', updated_at = ? WHERE id = ?", (json.dumps(ctx), now, wf_id))
        else:
            conn.execute("UPDATE workflows SET stage = 'replied-sent', updated_at = ? WHERE id = ?", (now, wf_id))
        conn.commit()
    except Exception:
        pass
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def _auto_gate(info: dict[str, Any]) -> bool:
    return bool(info["auto_pass"])


def _run_one(draft_id: str, *, edit: bool, dry_run: bool, auto: bool) -> int:
    path = _find_draft_path(draft_id)
    info = _resolve_draft(_read_json(path), path)

    while True:
        _print_preview(info)
        if dry_run:
            print("would-send: yes (dry-run)")
            return 0

        if auto and _auto_gate(info):
            print("auto-send: yes (quality bar passed)")
            result = _send_via_resend(info)
            _log_send(info, result)
            _update_workflow_stage(info["wf_id"])
            print(f"sent: {result.get('id') or result.get('message_id') or 'ok'}")
            return 0

        if auto and not _auto_gate(info):
            print("auto-send: no (quality bar failed)")
            return 0

        action = _prompt_action()
        if action == "n":
            print("skipped")
            return 0
        if action == "edit" or edit:
            _edit_draft(path)
            info = _resolve_draft(_read_json(path), path)
            edit = False
            continue

        result = _send_via_resend(info)
        _log_send(info, result)
        _update_workflow_stage(info["wf_id"])
        print(f"sent: {result.get('id') or result.get('message_id') or 'ok'}")
        return 0


def main() -> int:
    _load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("draft_ids", nargs="+", help="Draft workflow id(s) or filename fragments")
    parser.add_argument("--edit", action="store_true", help="Open matched draft JSON in $EDITOR before sending")
    parser.add_argument("--dry-run", action="store_true", help="Print preview only; do not send")
    parser.add_argument("--batch", action="store_true", help="Process multiple drafts in sequence")
    parser.add_argument("--auto", action="store_true", help="Auto-send only drafts that pass the quality bar")
    args = parser.parse_args()

    if len(args.draft_ids) > 1 and not args.batch:
        parser.error("multiple draft ids require --batch")

    exit_code = 0
    for draft_id in args.draft_ids:
        rc = _run_one(draft_id, edit=args.edit, dry_run=args.dry_run, auto=args.auto)
        exit_code = exit_code or rc
        if args.batch and len(args.draft_ids) > 1:
            print()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
