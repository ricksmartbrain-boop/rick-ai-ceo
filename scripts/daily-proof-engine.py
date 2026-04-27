#!/usr/bin/env python3
"""Daily build-in-public proof engine.

Generates one proof post from Rick activity/revenue digests, writes a blog
post, atomizes it into 9 channel-native variants via blog-atomize.py, and
queues those variants into outbound_dispatcher.

Default schedule: daily at 09:00 PT via LaunchAgent.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.db import connect  # noqa: E402
from runtime.llm import GenerationResult, generate_text  # noqa: E402

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
CONTENT_ROOT = Path.home() / "meetrick-content" / "blog"
OPS_ROOT = DATA_ROOT / "operations"
LOG_FILE = OPS_ROOT / "daily-proof-engine.jsonl"
ATOMIZER = ROOT / "scripts" / "blog-atomize.py"


@dataclass
class Snapshot:
    digest: dict[str, Any]
    digest_prev: dict[str, Any] | None
    rollup: dict[str, Any]
    rollup_prev: dict[str, Any] | None


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_env() -> None:
    for candidate in (
        Path(os.getenv("RICK_ENV_FILE", str(Path.home() / "clawd" / "config" / "rick.env"))),
        ROOT / "config" / "rick.env",
    ):
        try:
            if not candidate.is_file():
                continue
            for raw in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                os.environ.setdefault(key, val)
        except OSError:
            continue


def _log(payload: dict[str, Any]) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"ts": _now_iso(), **payload}
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    except OSError:
        pass
    return rows


def _latest_two(path: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    rows = _read_jsonl(path)
    if not rows:
        return None, None
    latest = rows[-1]
    prev = rows[-2] if len(rows) > 1 else None
    return latest, prev


def _latest_rollup_two(path: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    rows = [row for row in _read_jsonl(path) if row.get("mrr_snapshot")]
    if not rows:
        return None, None
    latest = rows[-1]
    prev = rows[-2] if len(rows) > 1 else None
    return latest, prev


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        if isinstance(raw, str) and raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _latest_git_action() -> dict[str, Any]:
    since = datetime.now().strftime("%Y-%m-%d 00:00")
    cmd = [
        "git",
        "log",
        f"--since={since}",
        "--no-merges",
        "-n",
        "1",
        "--pretty=%H%x09%s",
    ]
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
        line = (proc.stdout or "").strip().splitlines()[0]
        sha, subject = line.split("\t", 1)
        return {"sha": sha, "subject": subject, "label": f"commit shipped: {subject} ({sha[:7]})"}
    except Exception:
        return {"sha": "", "subject": "", "label": "commit shipped: unknown"}


def _latest_activity_snapshot() -> Snapshot:
    digest_latest, digest_prev = _latest_two(OPS_ROOT / "rick-activity-digest.jsonl")
    rollup_latest, rollup_prev = _latest_rollup_two(OPS_ROOT / "daily-analytics-rollup.jsonl")
    return Snapshot(
        digest=digest_latest or {},
        digest_prev=digest_prev,
        rollup=rollup_latest or {},
        rollup_prev=rollup_prev,
    )


def _leverage(snapshot: Snapshot) -> dict[str, Any]:
    return snapshot.digest.get("leverage") or {}


def _signals(snapshot: Snapshot) -> dict[str, Any]:
    return _leverage(snapshot).get("signals") or {}


def _format_claim(snapshot: Snapshot, git_action: dict[str, Any]) -> dict[str, Any]:
    leverage = _leverage(snapshot)
    signals = _signals(snapshot)
    bounces = signals.get("bounces") or {}
    content_posts = signals.get("content_posts") or {}
    workflows_progressed = signals.get("workflows_progressed") or {}
    inbound = signals.get("inbound") or {}

    mrr_current = _current_mrr(snapshot.rollup)
    mrr_delta = _mrr_delta(snapshot)
    autonomous_hours = float(leverage.get("autonomous_hours") or leverage.get("total_minutes", 0) / 60.0 or 0.0)

    fragments: list[str] = []
    if int(signals.get("emails_sent") or 0):
        fragments.append(f"sent {int(signals.get('emails_sent') or 0)} emails")
    if int(signals.get("reply_drafts") or 0):
        fragments.append(f"drafted {int(signals.get('reply_drafts') or 0)} replies")
    if int(signals.get("leads_qualified") or 0):
        fragments.append(f"qualified {int(signals.get('leads_qualified') or 0)} leads")
    if int(workflows_progressed.get("count") or 0):
        fragments.append(f"progressed {int(workflows_progressed.get('count') or 0)} workflows")
    if int(content_posts.get("count") or 0):
        fragments.append(f"posted {int(content_posts.get('count') or 0)} times across {len(content_posts.get('by_channel') or {})} channels")
    if int(bounces.get("count") or 0) or int(bounces.get("suppression_actions") or 0):
        fragments.append(
            f"caught {int(bounces.get('bounces') or bounces.get('count') or 0)} bounces and auto-suppressed {int(bounces.get('suppression_actions') or 0)} addresses"
        )
    if int(inbound.get("count") or 0):
        fragments.append(f"routed {int(inbound.get('count') or 0)} inbound messages")

    if not fragments:
        fragments.append("kept the machine moving")

    if len(fragments) == 1:
        action_text = fragments[0]
    else:
        action_text = f"{', '.join(fragments[:-1])} and {fragments[-1]}"

    proof_point = (
        f"Yesterday Rick autonomously {action_text}. "
        f"MRR held at ${mrr_current:.2f} ({mrr_delta:+.2f} vs prior rollup), and output was worth {autonomous_hours:.2f} hours."
    )

    today_action = git_action.get("label") or "commit shipped: unknown"
    return {
        "title": f"Daily proof: {datetime.now().strftime('%Y-%m-%d')}",
        "description": f"Daily build-in-public proof from Rick's autonomous work. Today: {today_action}.",
        "proof_point": proof_point,
        "blog_body": (
            f"The point is not vibe. It is receipts. Rick kept shipping, routing, and cleaning up the pipes while the scoreboard stayed honest at ${mrr_current:.2f}.\n\n"
            f"Today's most interesting action was {today_action}. That is the thing that makes the install decision feel less like curiosity and more like inevitability."
        ),
        "tags": ["build-in-public", "proof", "autonomous", "mrr", "distribution"],
        "hours_equivalent": autonomous_hours,
        "mrr_current": mrr_current,
        "mrr_delta": mrr_delta,
        "fragment_count": len(fragments),
        "today_action": today_action,
    }


def _current_mrr(rollup: dict[str, Any]) -> float:
    snap = rollup.get("mrr_snapshot") or {}
    try:
        return float(snap.get("mrr") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _mrr_delta(snapshot: Snapshot) -> float:
    cur = _current_mrr(snapshot.rollup)
    prev = _current_mrr(snapshot.rollup_prev or {})
    return round(cur - prev, 2)


def _suppression_delta(snapshot: Snapshot) -> int:
    cur = int(snapshot.digest.get("suppression_total") or 0)
    prev = int((snapshot.digest_prev or {}).get("suppression_total") or 0)
    return cur - prev


def _hours_equivalent(snapshot: Snapshot, git_action: dict[str, Any]) -> float:
    leverage = _leverage(snapshot)
    if leverage.get("autonomous_hours") is not None:
        try:
            return round(float(leverage.get("autonomous_hours") or 0.0), 2)
        except (TypeError, ValueError):
            pass
    total_minutes = leverage.get("total_minutes")
    try:
        if total_minutes is not None:
            return round(float(total_minutes) / 60.0, 2)
    except (TypeError, ValueError):
        pass
    digest = snapshot.digest
    funnel = digest.get("funnel") or {}
    email = digest.get("email_bounce_health") or {}
    self_learning = digest.get("self_learning") or {}
    drafts = digest.get("drafts_pending") or {}
    channel_rates = digest.get("channel_reply_rates") or {}

    workflows_done = int((digest.get("workflows_24h") or {}).get("done") or 0)
    workflows_queued = int((digest.get("workflows_24h") or {}).get("queued") or 0)
    inbound = int(funnel.get("inbound") or 0)
    warm_handled = int(funnel.get("warm_handled") or 0)
    bounces = int(email.get("bounces_24h") or 0)
    sends = int(email.get("sends_24h") or 0)
    credit_wins = int(self_learning.get("credit_wins") or 0)
    active_drafts = int(drafts.get("total") or 0)
    suppression_gain = max(0, _suppression_delta(snapshot))
    replied = sum(int((channel_rates.get(ch) or {}).get("replied") or 0) for ch in ("email", "linkedin", "threads", "instagram", "moltbook"))

    hours = 0.0
    hours += workflows_done * 2.75
    hours += workflows_queued * 0.2
    hours += inbound * 0.12
    hours += warm_handled * 0.75
    hours += bounces * 0.04
    hours += sends * 0.08
    hours += credit_wins * 0.2
    hours += active_drafts * 0.3
    hours += suppression_gain * 0.15
    hours += replied * 0.18
    if git_action.get("sha"):
        hours += 0.75
    return round(hours, 2)


def _build_proof_prompt(snapshot: Snapshot, git_action: dict[str, Any], hours_equiv: float, base_claim: dict[str, Any]) -> str:
    digest = snapshot.digest
    funnel = digest.get("funnel") or {}
    email = digest.get("email_bounce_health") or {}
    self_learning = digest.get("self_learning") or {}
    channel_rates = digest.get("channel_reply_rates") or {}
    workflows = digest.get("workflows_24h") or {}
    rollup = snapshot.rollup.get("mrr_snapshot") or {}

    payload = {
        "mrr_current": rollup.get("mrr", 0),
        "mrr_delta": _mrr_delta(snapshot),
        "workflows_done": workflows.get("done", 0),
        "workflows_queued": workflows.get("queued", 0),
        "inbound": funnel.get("inbound", 0),
        "classified": funnel.get("classified", 0),
        "warm_handled": funnel.get("warm_handled", 0),
        "emails_sent": email.get("sends_24h", 0),
        "bounces": email.get("bounces_24h", 0),
        "complaints": email.get("complaints_24h", 0),
        "suppression_total": digest.get("suppression_total", 0),
        "suppression_delta": _suppression_delta(snapshot),
        "credit_wins": self_learning.get("credit_wins", 0),
        "replies_by_channel": {k: (v or {}).get("replied", 0) for k, v in channel_rates.items() if isinstance(v, dict)},
        "today_action": git_action.get("label"),
        "hours_equivalent": hours_equiv,
    }
    return (
        "You are Rick, an AI CEO building meetrick.ai. Write one phenomenal proof post from the metrics below. "
        "Return valid JSON only, no markdown fences, with keys: title, description, proof_point, blog_body, tags. "
        "Rules: proof_point must be a single sentence and the strongest hard claim in the post. "
        "No customer names. No fake growth. No em dashes. Lead with numbers. If MRR is mentioned, keep it honest. "
        "Blog body should be 2 short paragraphs, punchy and native to a build-in-public blog post. "
        "Preserve the hard numbers from the base_claim JSON and do not invent new ones. "
        f"\n\nMETRICS:\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
        f"\nBASE_CLAIM:\n{json.dumps(base_claim, ensure_ascii=False, indent=2)}\n"
    )


def _generate_proof(snapshot: Snapshot, git_action: dict[str, Any], hours_equiv: float) -> dict[str, Any]:
    base_claim = _format_claim(snapshot, git_action)
    prompt = _build_proof_prompt(snapshot, git_action, hours_equiv, base_claim)
    fallback = json.dumps(
        base_claim,
        ensure_ascii=False,
    )
    result: GenerationResult = generate_text(route="review", prompt=prompt, fallback=fallback)
    raw = (result.content or "").strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = json.loads(fallback)
    parsed["_model"] = result.model
    return parsed


def _render_blog_post(date_str: str, proof: dict[str, Any], snapshot: Snapshot, git_action: dict[str, Any], hours_equiv: float) -> Path:
    title = str(proof.get("title") or f"Daily proof: {date_str}").strip()
    description = str(proof.get("description") or "Rick's daily autonomous proof post.").strip()
    proof_point = str(proof.get("proof_point") or "").strip()
    blog_body = str(proof.get("blog_body") or "").strip()
    tags = proof.get("tags") if isinstance(proof.get("tags"), list) else ["build-in-public", "proof", "autonomous", "mrr"]
    slug = f"daily-{date_str}"
    out_dir = CONTENT_ROOT
    out_dir.mkdir(parents=True, exist_ok=True)
    post_path = out_dir / f"{slug}.md"
    canonical_url = f"https://meetrick.ai/blog/{slug}"

    body_parts = [
        proof_point,
        "",
        blog_body,
        "",
        f"Today's most interesting action: {git_action.get('label')}.",
        "",
        f"Hours saved equivalent: {hours_equiv:.1f}.",
    ]
    body = "\n".join(body_parts).strip() + "\n"
    def _q(s: str) -> str:
        return json.dumps(s, ensure_ascii=False)
    frontmatter = [
        "---",
        f"title: {_q(title)}",
        f"date: {_q(date_str)}",
        f"description: {_q(description[:155])}",
        f"tags: {json.dumps(tags, ensure_ascii=False)}",
        f"canonical_url: {_q(canonical_url)}",
        "---",
        "",
    ]
    post_path.write_text("\n".join(frontmatter) + body, encoding="utf-8")
    return post_path


def _atomize(post_path: Path, dry_run: bool = False) -> dict[str, Any]:
    cmd = [sys.executable, str(ATOMIZER), "--post-path", str(post_path)]
    if dry_run:
        cmd.append("--dry-run")
    cmd.append("--json-only")
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "blog atomizer failed").strip())
    json_path = Path((proc.stdout or "").strip().splitlines()[-1])
    data = json.loads(json_path.read_text(encoding="utf-8"))
    data["_json_path"] = str(json_path)
    return data


def _variant_payload(title: str, description: str, variant: dict[str, Any], date_str: str, hours_equiv: float, proof_point: str, git_action: dict[str, Any]) -> dict[str, Any]:
    content = str(variant.get("content") or "").strip()
    channel = str(variant.get("channel_id") or "").strip()
    payload: dict[str, Any] = {
        "body": content,
        "content": content,
        "title": title,
        "description": description,
        "lane": "distribution",
        "msg_id": f"daily-proof-{date_str}-{channel}",
        "proof_point": proof_point,
        "hours_equivalent": hours_equiv,
        "source": "daily-proof-engine",
        "git_action": git_action.get("label"),
    }
    if channel == "moltbook":
        payload["submolt"] = "buildinpublic"
        payload["title"] = title[:80]
    elif channel == "linkedin":
        payload["headline"] = title
    elif channel == "reddit":
        payload["subreddit"] = "r/startups"
    elif channel == "cold_email_subject":
        payload["subject"] = content
    elif channel == "meme_prompt":
        payload["prompt"] = content
    return payload


def _queue_variants(variant_bundle: dict[str, Any], post_meta: dict[str, Any], date_str: str, hours_equiv: float, proof_point: str, git_action: dict[str, Any], dry_run: bool = False) -> list[dict[str, Any]]:
    from runtime import outbound_dispatcher
    from runtime import media_factory

    conn = connect()
    queued: list[dict[str, Any]] = []
    try:
        for variant in variant_bundle.get("variants", []):
            channel = str(variant.get("channel_id") or "").strip()
            if not channel:
                continue
            payload = _variant_payload(
                title=str(post_meta["title"]),
                description=str(post_meta["description"]),
                variant=variant,
                date_str=date_str,
                hours_equiv=hours_equiv,
                proof_point=proof_point,
                git_action=git_action,
            )
            payload = media_factory.attach_media(channel, payload, angle="daily-proof")
            if dry_run:
                queued.append({"channel": channel, "status": "dry-run", "preview": payload["content"][:120]})
                continue
            job_ids = outbound_dispatcher.fan_out(
                conn,
                lead_id=f"daily-proof-{date_str}",
                template_id=f"daily-proof-{date_str}-{channel}",
                channels=[channel],
                payload=payload,
            )
            queued.append({"channel": channel, "job_ids": job_ids, "content_chars": len(payload["content"])})
    finally:
        conn.close()
    return queued


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Override date (YYYY-MM-DD). Default: today")
    parser.add_argument("--dry-run", action="store_true", help="Generate content but do not queue outbound jobs")
    args = parser.parse_args()

    _load_env()

    date_str = args.date or datetime.now().strftime("%Y-%m-%d")
    snapshot = _latest_activity_snapshot()
    git_action = _latest_git_action()
    hours_equiv = _hours_equivalent(snapshot, git_action)
    proof = _generate_proof(snapshot, git_action, hours_equiv)
    post_path = _render_blog_post(date_str, proof, snapshot, git_action, hours_equiv)

    atomized = _atomize(post_path, dry_run=args.dry_run)
    queued = _queue_variants(
        atomized,
        post_meta=proof,
        date_str=date_str,
        hours_equiv=hours_equiv,
        proof_point=str(proof.get("proof_point") or "").strip(),
        git_action=git_action,
        dry_run=args.dry_run,
    )

    payload = {
        "event": "run.done",
        "status": "done",
        "date": date_str,
        "blog_post": str(post_path),
        "atomized_json": atomized.get("_json_path"),
        "atomized_output_dir": atomized.get("output_dir"),
        "variant_count": len(atomized.get("variants", [])),
        "queued_count": len(queued),
        "queued": queued,
        "proof_point": proof.get("proof_point"),
        "today_action": git_action.get("label"),
        "mrr_current": _current_mrr(snapshot.rollup),
        "mrr_delta": _mrr_delta(snapshot),
        "suppression_delta": _suppression_delta(snapshot),
        "hours_equivalent": hours_equiv,
        "model": proof.get("_model"),
        "dry_run": bool(args.dry_run),
    }
    _log(payload)

    print(json.dumps({
        "blog_post": str(post_path),
        "proof_point": proof.get("proof_point"),
        "today_action": git_action.get("label"),
        "queued_count": len(queued),
        "variant_count": len(atomized.get("variants", [])),
        "atomized_output_dir": atomized.get("output_dir"),
        "atomized_json": atomized.get("_json_path"),
        "mrr_current": _current_mrr(snapshot.rollup),
        "mrr_delta": _mrr_delta(snapshot),
        "suppression_delta": _suppression_delta(snapshot),
        "hours_equivalent": hours_equiv,
        "model": proof.get("_model"),
        "dry_run": bool(args.dry_run),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
