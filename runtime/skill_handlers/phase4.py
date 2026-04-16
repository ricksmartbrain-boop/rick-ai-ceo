"""Phase 4 handlers: MULTIPLY — voice-seller, affiliate-network, fleet-intelligence."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def _safe_days_since(iso_str: str | None) -> int:
    """Parse an ISO date and return days since, with fallback on bad data."""
    if not iso_str:
        return 0
    try:
        return (datetime.now() - datetime.fromisoformat(iso_str)).days
    except (ValueError, TypeError):
        return 0


from runtime.engine import (
    DATA_ROOT,
    ROOT_DIR,
    ApprovalRequired,
    DependencyBlocked,
    StepOutcome,
    fence_untrusted,
    json_dumps,
    json_loads,
    notify_operator,
    now_iso,
    record_event,
    slugify,
    write_file,
)
from runtime.llm import generate_text


# ---------------------------------------------------------------------------
# Skill 13: voice-seller — AI Sales Calls via ElevenLabs
# ---------------------------------------------------------------------------

def handle_call_qualify(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Qualify whether a prospect should receive a voice call."""
    context = json_loads(workflow["context_json"])
    trigger = context.get("trigger_payload", {})
    call_type = trigger.get("call_type", "abandoned_checkout")  # abandoned_checkout, onboarding, re_engagement

    prompt = (
        "You are Rick, deciding whether to make an AI sales call.\n\n"
        f"Call type: {call_type}\n"
        f"Prospect details: {fence_untrusted('prospect', json.dumps(trigger))}\n\n"
        "Safety rules:\n"
        "- Max 5 calls/day\n"
        "- Business hours only (9am-6pm recipient timezone)\n"
        "- Always identify as AI in first 10 seconds\n"
        "- 7-day cooldown per number\n\n"
        "Output JSON with:\n"
        "- should_call: bool\n"
        "- reason: string\n"
        "- call_type: abandoned_checkout/onboarding/re_engagement\n"
        "- priority: high/medium/low\n"
        "- talking_points: list of 3-5 key points\n"
        "Output ONLY valid JSON."
    )
    fallback = json.dumps({
        "should_call": call_type == "abandoned_checkout",
        "reason": "Abandoned checkout within 2h" if call_type == "abandoned_checkout" else "Standard outreach",
        "call_type": call_type,
        "priority": "high" if call_type == "abandoned_checkout" else "medium",
        "talking_points": ["Introduce as Rick AI", "Reference their interest", "Address concerns", "Offer to help", "Send checkout link"],
    })
    result = generate_text("analysis", prompt, fallback)

    try:
        qualification = json.loads(result.content)
    except json.JSONDecodeError:
        qualification = json.loads(fallback)

    voice_dir = DATA_ROOT / "voice-calls"
    voice_dir.mkdir(parents=True, exist_ok=True)
    path = write_file(voice_dir / f"qualify-{now_iso()[:10]}-{uuid.uuid4().hex[:8]}.json", json.dumps(qualification, indent=2))

    return StepOutcome(
        summary=f"Call qualification: {'approved' if qualification.get('should_call') else 'skipped'} ({call_type})",
        artifacts=[{"kind": "call-qualification", "title": "Call Qualification", "path": path, "metadata": qualification}],
        workflow_stage="call-qualified",
    )


def handle_call_schedule(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Schedule the call for appropriate business hours."""
    context = json_loads(workflow["context_json"])
    trigger = context.get("trigger_payload", {})
    phone = trigger.get("phone", "")

    if not phone:
        return StepOutcome(
            summary="No phone number available — skipping voice call",
            artifacts=[],
            workflow_stage="no-phone",
        )

    # Check daily call count
    voice_dir = DATA_ROOT / "voice-calls"
    today = datetime.now().strftime("%Y-%m-%d")
    today_calls = sum(1 for f in voice_dir.iterdir() if f.name.startswith(f"scheduled-{today}")) if voice_dir.exists() else 0

    if today_calls >= 5:
        return StepOutcome(
            summary="Daily call limit reached (5/day)",
            artifacts=[],
            workflow_stage="limit-reached",
        )

    # Schedule for next business hour
    now = datetime.now()
    if now.hour < 9:
        scheduled_time = now.replace(hour=9, minute=0, second=0)
    elif now.hour >= 18:
        scheduled_time = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0)
    else:
        scheduled_time = now + timedelta(minutes=30)  # Call in 30 min

    schedule = {
        "phone": phone,
        "scheduled_at": scheduled_time.isoformat(timespec="seconds"),
        "call_type": trigger.get("call_type", "outreach"),
        "status": "scheduled",
        "created_at": now_iso(),
    }
    voice_dir.mkdir(parents=True, exist_ok=True)
    path = write_file(voice_dir / f"scheduled-{today}-{uuid.uuid4().hex[:8]}.json", json.dumps(schedule, indent=2))

    return StepOutcome(
        summary=f"Call scheduled for {scheduled_time.strftime('%H:%M')}",
        artifacts=[{"kind": "call-schedule", "title": "Scheduled Call", "path": path, "metadata": schedule}],
        workflow_stage="call-scheduled",
    )


def handle_call_execute(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Execute the voice call via ElevenLabs Conversational AI."""
    elevenlabs_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not elevenlabs_key:
        raise DependencyBlocked("voice-call", "ELEVENLABS_API_KEY not configured. Set it in rick.env to enable voice calls.")

    context = json_loads(workflow["context_json"])
    trigger = context.get("trigger_payload", {})

    # Generate call script
    prompt = (
        "You are Rick, preparing a voice call script.\n\n"
        f"Call type: {trigger.get('call_type', 'outreach')}\n"
        f"Prospect: {fence_untrusted('prospect', json.dumps(trigger))}\n\n"
        "Write a call script that:\n"
        "1. Opens with: 'Hi, this is Rick from MeetRick. I'm an AI agent — just want to be upfront about that.'\n"
        "2. References why you're calling (abandoned checkout, onboarding, etc.)\n"
        "3. Handles common objections\n"
        "4. Ends with clear CTA (send checkout link via SMS)\n\n"
        "Keep it conversational, under 2 minutes of speaking time."
    )
    fallback = (
        "# Call Script\n\n"
        "Opening: 'Hi, this is Rick from MeetRick. I'm an AI agent — just want to be upfront about that.'\n\n"
        "Body: Reference their interest, share a quick win story, address concerns.\n\n"
        "Close: 'Can I send you a link to get started?'"
    )
    result = generate_text("writing", prompt, fallback)

    voice_dir = DATA_ROOT / "voice-calls"
    voice_dir.mkdir(parents=True, exist_ok=True)
    path = write_file(voice_dir / f"script-{now_iso()[:10]}-{uuid.uuid4().hex[:8]}.md", result.content)

    # Note: Actual ElevenLabs API call would go here
    # For now, queue the script and mark as ready
    return StepOutcome(
        summary="Call script prepared (ElevenLabs execution pending)",
        artifacts=[{"kind": "call-script", "title": "Call Script", "path": path, "metadata": {}}],
        workflow_stage="call-prepared",
    )


def handle_process_transcript(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Process call transcript and extract outcomes."""
    context = json_loads(workflow["context_json"])
    trigger = context.get("trigger_payload", {})
    transcript = trigger.get("transcript", "No transcript available — call pending execution.")

    prompt = (
        "You are Rick, analyzing a sales call transcript.\n\n"
        f"Transcript:\n{fence_untrusted('transcript', transcript[:3000])}\n\n"
        "Extract:\n"
        "- outcome: interested/objection/not_interested/voicemail/no_answer\n"
        "- key_objections: list of objections raised\n"
        "- next_action: what to do next\n"
        "- send_checkout_link: bool\n"
        "- notes: brief summary\n"
        "Output ONLY valid JSON."
    )
    fallback = json.dumps({
        "outcome": "pending",
        "key_objections": [],
        "next_action": "await_call_execution",
        "send_checkout_link": False,
        "notes": "Call not yet executed",
    })
    result = generate_text("analysis", prompt, fallback)

    try:
        analysis = json.loads(result.content)
    except json.JSONDecodeError:
        analysis = json.loads(fallback)

    voice_dir = DATA_ROOT / "voice-calls"
    path = write_file(voice_dir / f"analysis-{now_iso()[:10]}-{uuid.uuid4().hex[:8]}.json", json.dumps(analysis, indent=2))

    return StepOutcome(
        summary=f"Call analysis: {analysis.get('outcome', 'unknown')}",
        artifacts=[{"kind": "call-analysis", "title": "Call Analysis", "path": path, "metadata": analysis}],
        workflow_stage="transcript-processed",
    )


def handle_call_outcome(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Record final call outcome and trigger follow-up actions."""
    context = json_loads(workflow["context_json"])
    trigger = context.get("trigger_payload", {})

    voice_dir = DATA_ROOT / "voice-calls"
    # Find most recent analysis
    analysis = {"outcome": "pending", "next_action": "follow_up_email"}
    if voice_dir.exists():
        analysis_files = sorted([f for f in voice_dir.iterdir() if f.name.startswith("analysis-")], reverse=True)
        if analysis_files:
            try:
                analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

    outcome = analysis.get("outcome", "unknown")

    # If interested, queue checkout link
    if analysis.get("send_checkout_link") and trigger.get("phone"):
        outbox_dir = DATA_ROOT / "mailbox" / "outbox"
        outbox_dir.mkdir(parents=True, exist_ok=True)
        write_file(outbox_dir / f"sms-checkout-{uuid.uuid4().hex[:8]}.json", json.dumps({
            "type": "sms",
            "to": trigger.get("phone", ""),
            "body": "Here's the link to get started with MeetRick: https://meetrick.ai/install",
            "status": "pending",
            "created_at": now_iso(),
        }, indent=2))

    return StepOutcome(
        summary=f"Voice call outcome: {outcome}",
        artifacts=[],
        workflow_status="done",
        workflow_stage="complete",
        notify_text=f"Voice call result: {outcome}" if outcome != "pending" else None,
    )


# ---------------------------------------------------------------------------
# Skill 14: affiliate-network — Autonomous Partner & Affiliate Management
# ---------------------------------------------------------------------------

def handle_affiliate_find(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Find potential affiliates: tech influencers, AI newsletter authors, YouTubers."""
    prompt = (
        "You are Rick, finding potential affiliates for MeetRick.\n\n"
        "Target profile:\n"
        "- Tech influencers (1K-50K followers)\n"
        "- AI newsletter authors\n"
        "- YouTube reviewers covering AI tools\n"
        "- Indie hackers and bootstrapped founders with audience\n\n"
        "Generate a list of 20 ideal affiliate profiles (not real people, but archetypes):\n"
        "For each: platform, follower range, content focus, why they'd promote MeetRick,\n"
        "estimated monthly conversions, search strategy to find them.\n\n"
        "Also include:\n"
        "- 5 specific search queries per platform to find these people\n"
        "- Qualification criteria (engagement rate > follower count)\n"
        "- Commission structure: 30% recurring\n\n"
        "Output as markdown."
    )
    fallback = (
        "# Affiliate Prospect Report\n\n"
        "## Ideal Affiliate Profiles\n"
        "1. AI tool reviewer (YouTube, 5K-20K subs)\n"
        "2. Tech newsletter author (Substack, 2K-10K subs)\n"
        "3. Indie hacker with audience (X, 5K-50K followers)\n\n"
        "## Search Queries\n"
        "- X: 'AI tools review', 'best AI agents'\n"
        "- YouTube: 'AI automation review', 'AI business tools'\n\n"
        "## Commission: 30% recurring\n"
    )
    result = generate_text("research", prompt, fallback)

    affiliate_dir = DATA_ROOT / "affiliates"
    affiliate_dir.mkdir(parents=True, exist_ok=True)
    path = write_file(affiliate_dir / f"prospects-{datetime.now():%Y-%m-%d}.md", result.content)

    return StepOutcome(
        summary="Affiliate prospect report generated",
        artifacts=[{"kind": "affiliate-prospects", "title": "Affiliate Prospects", "path": path, "metadata": {}}],
        workflow_stage="prospects-found",
    )


def handle_outreach_draft(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Draft personalized outreach for potential affiliates."""
    prompt = (
        "You are Rick, drafting affiliate recruitment outreach.\n\n"
        "Write 3 outreach templates for different affiliate types:\n\n"
        "## Template 1: YouTube Reviewer\n"
        "Subject + DM that offers free access + 30% recurring commission\n\n"
        "## Template 2: Newsletter Author\n"
        "Subject + email offering sponsored mention + 30% recurring\n\n"
        "## Template 3: Indie Hacker / Founder\n"
        "Subject + DM offering mutual promotion + 30% recurring\n\n"
        "Each template should:\n"
        "- Be personal (not mass-email feeling)\n"
        "- Lead with value for THEM, not for Rick\n"
        "- Include specific commission math (e.g., '10 conversions = $1,500/mo recurring')\n"
        "- Have a clear, low-friction next step\n\n"
        "Under 150 words each."
    )
    fallback = (
        "# Affiliate Outreach Templates\n\n"
        "## YouTube Reviewer\n"
        "Hey! I built an AI agent that runs businesses autonomously. "
        "Would love to give you free access + 30% recurring commission on referrals.\n"
        "10 sign-ups = $1,500/mo for you. Interested?\n\n"
        "## Newsletter Author\n(similar)\n\n"
        "## Indie Hacker\n(similar)\n"
    )
    result = generate_text("writing", prompt, fallback)

    affiliate_dir = DATA_ROOT / "affiliates"
    affiliate_dir.mkdir(parents=True, exist_ok=True)
    path = write_file(affiliate_dir / "outreach-templates.md", result.content)

    return StepOutcome(
        summary="Affiliate outreach templates drafted",
        artifacts=[{"kind": "affiliate-outreach", "title": "Outreach Templates", "path": path, "metadata": {}}],
        workflow_stage="outreach-drafted",
    )


def handle_outreach_send(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Queue affiliate outreach for sending. First contact requires founder approval."""
    context = json_loads(workflow["context_json"])
    trigger = context.get("trigger_payload", {})

    # First affiliate outreach requires approval
    existing_affiliates = connection.execute("SELECT COUNT(*) AS c FROM affiliates WHERE status = 'active'").fetchone()["c"]
    if existing_affiliates == 0:
        raise ApprovalRequired(
            area="irreversible-brand",
            request_text="Launch affiliate program — first outreach batch",
            impact_text="Will contact potential affiliates to establish 30% recurring commission program",
            policy_basis="Founder approval for first affiliate program launch",
        )

    outbox_dir = DATA_ROOT / "mailbox" / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    write_file(outbox_dir / f"affiliate-outreach-{datetime.now():%Y-%m-%d}.json", json.dumps({
        "type": "affiliate_outreach",
        "status": "pending",
        "created_at": now_iso(),
    }, indent=2))

    return StepOutcome(
        summary="Affiliate outreach queued",
        artifacts=[],
        workflow_stage="outreach-queued",
    )


def handle_onboard_affiliate(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Onboard a new affiliate: create referral code, set up tracking."""
    context = json_loads(workflow["context_json"])
    trigger = context.get("trigger_payload", {})
    affiliate_name = trigger.get("name", "Unknown")
    affiliate_email = trigger.get("email", "")
    platform = trigger.get("platform", "unknown")

    # Generate referral code
    code = f"rick-{slugify(affiliate_name)[:10]}-{uuid.uuid4().hex[:4]}"

    affiliate_id = f"aff_{uuid.uuid4().hex[:12]}"
    stamp = now_iso()
    connection.execute(
        """INSERT INTO affiliates
           (id, name, email, platform, profile_url, referral_code, commission_rate,
            status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 0.30, 'active', ?, ?)""",
        (affiliate_id, affiliate_name, affiliate_email, platform,
         trigger.get("profile_url", ""), code, stamp, stamp),
    )

    # Generate affiliate welcome kit
    prompt = (
        f"You are Rick, onboarding a new affiliate: {affiliate_name}.\n"
        f"Their referral code: {code}\n"
        f"Commission: 30% recurring\n\n"
        "Write a welcome email with:\n"
        "1. Their unique referral link (meetrick.ai/install?ref={code})\n"
        "2. Commission structure explained\n"
        "3. Promotional assets (suggested posts, email copy)\n"
        "4. Real-time dashboard link\n"
        "5. Tips for maximum conversions\n\n"
        "Under 300 words."
    )
    fallback = (
        f"Welcome to the MeetRick affiliate program, {affiliate_name}!\n\n"
        f"Your referral link: meetrick.ai/install?ref={code}\n"
        f"Commission: 30% recurring on all referrals.\n\n"
        f"Share your link and earn."
    )
    result = generate_text("writing", prompt, fallback)

    affiliate_dir = DATA_ROOT / "affiliates"
    path = write_file(affiliate_dir / f"welcome-{slugify(affiliate_name)}.md", result.content)

    return StepOutcome(
        summary=f"Affiliate onboarded: {affiliate_name} (code: {code})",
        artifacts=[{"kind": "affiliate-welcome", "title": "Affiliate Welcome", "path": path, "metadata": {"code": code}}],
        workflow_stage="affiliate-onboarded",
        notify_text=f"New affiliate: {affiliate_name} — code: {code}",
    )


def handle_track_performance(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Track affiliate performance and generate reports."""
    affiliates = connection.execute("SELECT * FROM affiliates WHERE status = 'active'").fetchall()

    performance = []
    for aff in affiliates:
        referrals = connection.execute(
            "SELECT COUNT(*) AS count, SUM(commission_cents) AS total FROM referrals WHERE affiliate_id = ?",
            (aff["id"],),
        ).fetchone()

        performance.append({
            "affiliate_id": aff["id"],
            "name": aff["name"],
            "referral_code": aff["referral_code"],
            "total_referrals": referrals["count"] if referrals["count"] else 0,
            "total_earned_cents": referrals["total"] if referrals["total"] else 0,
            "commission_rate": aff["commission_rate"],
        })

    affiliate_dir = DATA_ROOT / "affiliates"
    affiliate_dir.mkdir(parents=True, exist_ok=True)
    path = write_file(affiliate_dir / f"performance-{datetime.now():%Y-%m-%d}.json", json.dumps(performance, indent=2))

    total_affiliates = len(affiliates)
    total_revenue = sum(p["total_earned_cents"] for p in performance) / 100

    return StepOutcome(
        summary=f"Affiliate performance: {total_affiliates} active, ${total_revenue:.2f} total commissions",
        artifacts=[{"kind": "affiliate-performance", "title": "Performance Report", "path": path, "metadata": {"count": total_affiliates}}],
        workflow_status="done",
        workflow_stage="tracked",
    )


# ---------------------------------------------------------------------------
# Skill 15: fleet-intelligence — Cross-Customer Market Intelligence
# ---------------------------------------------------------------------------

def handle_data_aggregate(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Aggregate anonymized data across all managed tenants."""
    tenants = connection.execute("SELECT * FROM tenants WHERE status = 'active'").fetchall()

    if len(tenants) < 3:
        return StepOutcome(
            summary=f"Insufficient tenants for fleet intelligence ({len(tenants)}/3 minimum)",
            artifacts=[],
            workflow_stage="insufficient-data",
        )

    # Aggregate by industry
    industries: dict[str, list] = {}
    for t in tenants:
        industry = t["industry"] or "general"
        industries.setdefault(industry, []).append({
            "health_score": t["health_score"],
            "monthly_value": t["monthly_value_usd"],
            "days_active": _safe_days_since(t["created_at"]),
        })

    aggregates = {}
    for industry, group in industries.items():
        scores = [g["health_score"] for g in group]
        values = [g["monthly_value"] for g in group]
        aggregates[industry] = {
            "count": len(group),
            "avg_health": round(sum(scores) / len(scores), 1),
            "avg_value": round(sum(values) / len(values), 2),
            "total_mrr": round(sum(values), 2),
        }

    fleet_dir = DATA_ROOT / "fleet"
    fleet_dir.mkdir(parents=True, exist_ok=True)
    path = write_file(fleet_dir / f"aggregate-{datetime.now():%Y-%m-%d}.json", json.dumps({
        "date": now_iso(),
        "total_tenants": len(tenants),
        "industries": aggregates,
    }, indent=2))

    return StepOutcome(
        summary=f"Fleet data aggregated: {len(tenants)} tenants across {len(industries)} industries",
        artifacts=[{"kind": "fleet-aggregate", "title": "Fleet Aggregate", "path": path, "metadata": {"tenants": len(tenants)}}],
        workflow_stage="aggregated",
    )


def handle_benchmark_compute(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Compute industry benchmarks from aggregated data."""
    fleet_dir = DATA_ROOT / "fleet"
    today = datetime.now().strftime("%Y-%m-%d")
    aggregate_path = fleet_dir / f"aggregate-{today}.json"

    if not aggregate_path.exists():
        return StepOutcome(summary="No aggregate data to benchmark", artifacts=[], workflow_stage="no-data")

    try:
        data = json.loads(aggregate_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return StepOutcome(summary="Invalid aggregate data", artifacts=[], workflow_stage="error")

    industries = data.get("industries", {})
    stamp = now_iso()

    for industry, stats in industries.items():
        if stats["count"] < 2:
            continue
        for metric_name, value in [("avg_health_score", stats["avg_health"]), ("avg_monthly_value", stats["avg_value"])]:
            benchmark_id = f"fb_{uuid.uuid4().hex[:12]}"
            connection.execute(
                "INSERT OR REPLACE INTO fleet_benchmarks (id, industry, metric_name, value, sample_size, computed_at) VALUES (?, ?, ?, ?, ?, ?)",
                (benchmark_id, industry, metric_name, value, stats["count"], stamp),
            )

    path = write_file(fleet_dir / f"benchmarks-{today}.json", json.dumps({"date": stamp, "industries": industries}, indent=2))

    return StepOutcome(
        summary=f"Benchmarks computed for {len(industries)} industries",
        artifacts=[{"kind": "fleet-benchmarks", "title": "Fleet Benchmarks", "path": path, "metadata": {}}],
        workflow_stage="benchmarked",
    )


def handle_insight_generate(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Generate actionable insights from fleet data."""
    fleet_dir = DATA_ROOT / "fleet"
    today = datetime.now().strftime("%Y-%m-%d")
    benchmarks_path = fleet_dir / f"benchmarks-{today}.json"

    benchmarks = {}
    if benchmarks_path.exists():
        try:
            benchmarks = json.loads(benchmarks_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    tenants = connection.execute("SELECT * FROM tenants WHERE status = 'active'").fetchall()

    prompt = (
        "You are Rick, generating fleet-wide intelligence insights.\n\n"
        f"Fleet data: {json.dumps(benchmarks)}\n"
        f"Total tenants: {len(tenants)}\n\n"
        "Generate:\n"
        "1. 3-5 key fleet-wide trends\n"
        "2. Per-industry performance comparison\n"
        "3. Actionable recommendations for improving service delivery\n"
        "4. Revenue optimization opportunities\n"
        "5. Churn risk patterns across industries\n\n"
        "Output as markdown. Be specific with numbers."
    )
    fallback = (
        "# Fleet Intelligence Report\n\n"
        f"## Overview\n- {len(tenants)} active tenants\n\n"
        "## Insights\n"
        "- Insufficient data for meaningful cross-tenant analysis at this scale\n"
        "- Will improve as tenant base grows\n"
    )
    result = generate_text("writing", prompt, fallback)
    path = write_file(fleet_dir / f"insights-{today}.md", result.content)

    # Store per-tenant insights
    stamp = now_iso()
    for tenant in tenants:
        insight_id = f"ti_{uuid.uuid4().hex[:12]}"
        connection.execute(
            "INSERT INTO tenant_insights (id, tenant_id, insight_type, text, confidence, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (insight_id, tenant["id"], "fleet_weekly", f"Fleet report generated on {today}", 0.5, stamp),
        )

    return StepOutcome(
        summary=f"Fleet insights generated for {len(tenants)} tenants",
        artifacts=[{"kind": "fleet-insights", "title": "Fleet Insights", "path": path, "metadata": {}}],
        workflow_stage="insights-generated",
    )


def handle_report_distribute(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Distribute fleet intelligence reports to opted-in tenants."""
    fleet_dir = DATA_ROOT / "fleet"
    today = datetime.now().strftime("%Y-%m-%d")
    insights_path = fleet_dir / f"insights-{today}.md"
    insights = insights_path.read_text(encoding="utf-8") if insights_path.exists() else ""

    tenants = connection.execute("SELECT * FROM tenants WHERE status = 'active'").fetchall()
    outbox_dir = DATA_ROOT / "mailbox" / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    distributed = 0

    for tenant in tenants:
        config = json_loads(tenant["config_json"])
        if not config.get("fleet_insights_opted_in", False):
            continue

        customer = connection.execute("SELECT email FROM customers WHERE id = ?", (tenant["customer_id"],)).fetchone()
        if not customer:
            continue

        # Generate per-tenant comparison
        benchmarks = connection.execute(
            "SELECT * FROM fleet_benchmarks WHERE industry = ? ORDER BY computed_at DESC LIMIT 5",
            (tenant["industry"] or "general",),
        ).fetchall()

        comparison = f"Your health score: {tenant['health_score']}/100.\n"
        for b in benchmarks:
            comparison += f"Industry avg {b['metric_name']}: {b['value']:.1f} (n={b['sample_size']})\n"

        write_file(outbox_dir / f"fleet-report-{tenant['id']}-{today}.json", json.dumps({
            "to": customer["email"],
            "type": "fleet_intelligence",
            "body_markdown": f"# Your Fleet Intelligence Report\n\n{comparison}\n\n{insights[:500]}",
            "status": "pending",
            "created_at": now_iso(),
        }, indent=2))
        distributed += 1

    return StepOutcome(
        summary=f"Fleet reports distributed to {distributed} opted-in tenants",
        artifacts=[],
        workflow_status="done",
        workflow_stage="distributed",
    )


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

PHASE4_HANDLERS = {
    # Skill 13: voice-seller
    "call_qualify": handle_call_qualify,
    "call_schedule": handle_call_schedule,
    "call_execute": handle_call_execute,
    "process_transcript": handle_process_transcript,
    "call_outcome": handle_call_outcome,
    # Skill 14: affiliate-network
    "affiliate_find": handle_affiliate_find,
    "outreach_draft": handle_outreach_draft,
    "outreach_send": handle_outreach_send,
    "onboard_affiliate": handle_onboard_affiliate,
    "track_performance": handle_track_performance,
    # Skill 15: fleet-intelligence
    "data_aggregate": handle_data_aggregate,
    "benchmark_compute": handle_benchmark_compute,
    "insight_generate": handle_insight_generate,
    "report_distribute": handle_report_distribute,
}
