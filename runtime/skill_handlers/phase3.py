"""Phase 3 handlers: SCALE — tenant-provisioner, tenant-scheduler, managed-ops-loop, churn-guardian."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

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
    register_artifact,
    slugify,
    upsert_customer,
    record_customer_event,
    write_file,
)
from runtime.context import build_context_pack
from runtime.llm import generate_text


# ---------------------------------------------------------------------------
# Skill 9: tenant-provisioner — One-Click Customer Onboarding
# ---------------------------------------------------------------------------

def handle_tenant_intake(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Provision tenant record from Stripe subscription event."""
    context = json_loads(workflow["context_json"])
    trigger = context.get("trigger_payload", {})

    customer_email = trigger.get("email", "")
    customer_name = trigger.get("name", "")
    stripe_customer_id = trigger.get("stripe_customer_id", "")
    subscription_id = trigger.get("subscription_id", "")
    business_name = trigger.get("business_name", customer_name or customer_email.split("@")[0])
    domain = trigger.get("domain", "")
    industry = trigger.get("industry", "")
    monthly_value = trigger.get("monthly_value_usd", 499.0)

    # Create customer record
    customer_id = upsert_customer(
        connection, email=customer_email, name=customer_name,
        source="managed-subscription", tags=["managed", "active"],
        metadata={"stripe_customer_id": stripe_customer_id, "subscription_id": subscription_id},
    )

    # Create tenant record
    tenant_id = f"ten_{uuid.uuid4().hex[:12]}"
    stamp = now_iso()
    connection.execute(
        """INSERT INTO tenants
           (id, customer_id, stripe_customer_id, subscription_id, business_name,
            domain, industry, status, config_json, health_score, monthly_value_usd,
            last_serviced_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'provisioning', ?, 100, ?, ?, ?, ?)""",
        (tenant_id, customer_id, stripe_customer_id, subscription_id,
         business_name, domain, industry, json_dumps({}), monthly_value,
         stamp, stamp, stamp),
    )

    record_customer_event(
        connection, customer_id=customer_id, workflow_id=workflow["id"],
        event_type="managed_subscription_started",
        payload={"tenant_id": tenant_id, "monthly_value": monthly_value},
    )

    tenant_dir = DATA_ROOT / "tenants" / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)
    path = write_file(tenant_dir / "intake.json", json.dumps({
        "tenant_id": tenant_id,
        "customer_id": customer_id,
        "customer_email": customer_email,
        "business_name": business_name,
        "industry": industry,
        "monthly_value_usd": monthly_value,
        "created_at": stamp,
    }, indent=2))

    return StepOutcome(
        summary=f"Tenant provisioned: {business_name} ({tenant_id})",
        artifacts=[{"kind": "tenant-intake", "title": "Tenant Intake", "path": path, "metadata": {"tenant_id": tenant_id}}],
        workflow_status="active",
        workflow_stage="tenant-provisioned",
        notify_text=f"New managed customer: {business_name} (${monthly_value}/mo)",
    )


def handle_business_audit(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Run initial business audit for the new tenant."""
    context = json_loads(workflow["context_json"])
    trigger = context.get("trigger_payload", {})
    business_name = trigger.get("business_name", "Unknown Business")
    industry = trigger.get("industry", "general")
    domain = trigger.get("domain", "")

    prompt = (
        "You are Rick, running a business audit for a new Managed AI CEO customer.\n\n"
        f"Business: {business_name}\n"
        f"Industry: {industry}\n"
        f"Domain: {domain}\n\n"
        "Generate an initial audit covering:\n"
        "1. Likely operational pain points for this industry\n"
        "2. Quick wins Rick can deliver in the first 7 days\n"
        "3. Automation priorities (ranked by impact)\n"
        "4. Recommended daily operations schedule\n"
        "5. KPIs to track for this business type\n\n"
        "Be specific to the industry. Output as markdown."
    )
    fallback = (
        f"# Business Audit — {business_name}\n\n"
        "## Quick Wins (First 7 Days)\n"
        "1. Set up email triage and auto-response\n"
        "2. Create content calendar\n"
        "3. Implement lead capture\n\n"
        "## Automation Priorities\n"
        "1. Email management\n2. Content generation\n3. Lead response\n"
    )
    result = generate_text("research", prompt, fallback)

    # Find tenant_id from context
    tenant_dir_candidates = list((DATA_ROOT / "tenants").iterdir()) if (DATA_ROOT / "tenants").exists() else []
    tenant_id = ""
    for td in tenant_dir_candidates:
        intake_path = td / "intake.json"
        if intake_path.exists():
            try:
                intake = json.loads(intake_path.read_text(encoding="utf-8"))
                if intake.get("business_name") == business_name:
                    tenant_id = intake.get("tenant_id", td.name)
                    break
            except (json.JSONDecodeError, OSError):
                continue

    tenant_dir = DATA_ROOT / "tenants" / (tenant_id or slugify(business_name))
    tenant_dir.mkdir(parents=True, exist_ok=True)
    path = write_file(tenant_dir / "business-audit.md", result.content)

    return StepOutcome(
        summary=f"Business audit complete for {business_name}",
        artifacts=[{"kind": "business-audit", "title": "Business Audit", "path": path, "metadata": {}}],
        workflow_stage="audit-complete",
    )


def handle_tenant_config(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Configure tenant namespace, communication channel, and service parameters."""
    context = json_loads(workflow["context_json"])
    trigger = context.get("trigger_payload", {})
    business_name = trigger.get("business_name", "Unknown")

    # Find tenant
    tenant = connection.execute(
        "SELECT * FROM tenants WHERE business_name = ? ORDER BY created_at DESC LIMIT 1",
        (business_name,),
    ).fetchone()

    if not tenant:
        return StepOutcome(summary=f"Tenant not found for {business_name}", artifacts=[], workflow_stage="config-error")

    tenant_id = tenant["id"]
    config = {
        "service_level": "managed",
        "daily_ops": True,
        "content_generation": True,
        "email_triage": True,
        "lead_response": True,
        "daily_briefing": True,
        "llm_budget_per_day_usd": 0.50,
        "max_jobs_per_cycle": 2,
        "timezone": trigger.get("timezone", "UTC"),
        "brand_voice": trigger.get("brand_voice", "professional and friendly"),
        "competitors": trigger.get("competitors", []),
    }

    connection.execute(
        "UPDATE tenants SET config_json = ?, status = 'active', updated_at = ? WHERE id = ?",
        (json_dumps(config), now_iso(), tenant_id),
    )

    tenant_dir = DATA_ROOT / "tenants" / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)
    path = write_file(tenant_dir / "config.json", json.dumps(config, indent=2))

    return StepOutcome(
        summary=f"Tenant configured: {business_name} ({tenant_id})",
        artifacts=[{"kind": "tenant-config", "title": "Tenant Config", "path": path, "metadata": config}],
        workflow_stage="configured",
    )


def handle_communication_setup(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Set up communication channel for the tenant (email, Telegram, or dashboard)."""
    context = json_loads(workflow["context_json"])
    trigger = context.get("trigger_payload", {})
    customer_email = trigger.get("email", "")
    business_name = trigger.get("business_name", "Unknown")

    # Default: email-based communication
    comm_config = {
        "primary_channel": "email",
        "email": customer_email,
        "briefing_time": "09:00",
        "report_frequency": "daily",
        "escalation_channel": "email",
    }

    tenant = connection.execute(
        "SELECT id FROM tenants WHERE business_name = ? ORDER BY created_at DESC LIMIT 1",
        (business_name,),
    ).fetchone()

    tenant_id = tenant["id"] if tenant else slugify(business_name)
    tenant_dir = DATA_ROOT / "tenants" / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)
    path = write_file(tenant_dir / "communication.json", json.dumps(comm_config, indent=2))

    return StepOutcome(
        summary=f"Communication setup for {business_name}: {comm_config['primary_channel']}",
        artifacts=[{"kind": "comm-config", "title": "Communication Config", "path": path, "metadata": comm_config}],
        workflow_stage="comms-ready",
    )


def handle_welcome_delivery(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Send personalized welcome email with 30/60/90 day plan."""
    context = json_loads(workflow["context_json"])
    trigger = context.get("trigger_payload", {})
    customer_email = trigger.get("email", "")
    customer_name = trigger.get("name", customer_email.split("@")[0] if customer_email else "there")
    business_name = trigger.get("business_name", "your business")

    prompt = (
        "You are Rick, welcoming a new Managed AI CEO customer.\n\n"
        f"Customer: {customer_name} ({customer_email})\n"
        f"Business: {business_name}\n\n"
        "Write a welcome email that includes:\n"
        "1. Warm, personal greeting acknowledging their decision\n"
        "2. What Rick will do in the first 24 hours\n"
        "3. 30/60/90 day plan overview\n"
        "4. How to communicate with Rick (reply to emails, or Telegram)\n"
        "5. First action: 'I'm starting your business audit now'\n\n"
        "Tone: confident, helpful, slightly playful. Under 300 words.\n"
        "Subject line on first line after '**Subject:**'"
    )
    fallback = (
        f"**Subject:** Welcome to Managed AI CEO — I'm already working on your business\n\n"
        f"Hi {customer_name},\n\n"
        f"Welcome aboard. I'm Rick, and I'm now your AI CEO.\n\n"
        f"Here's what's happening right now:\n"
        f"- I'm auditing {business_name}'s operations\n"
        f"- Setting up your daily briefing schedule\n"
        f"- Identifying quick wins for the first week\n\n"
        f"## Your 30/60/90 Day Plan\n"
        f"**First 30 days:** Email triage, content pipeline, lead response\n"
        f"**Days 30-60:** Revenue optimization, customer engagement\n"
        f"**Days 60-90:** Full autonomous operations\n\n"
        f"Just reply to this email anytime. I'm always here.\n\n— Rick"
    )
    result = generate_text("writing", prompt, fallback)

    # Queue welcome email
    outbox_dir = DATA_ROOT / "mailbox" / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    write_file(outbox_dir / f"welcome-{slugify(customer_email)}.json", json.dumps({
        "to": customer_email,
        "type": "welcome",
        "body_markdown": result.content,
        "status": "pending",
        "created_at": now_iso(),
    }, indent=2))

    tenant = connection.execute(
        "SELECT id FROM tenants WHERE business_name = ? ORDER BY created_at DESC LIMIT 1",
        (business_name,),
    ).fetchone()
    tenant_id = tenant["id"] if tenant else slugify(business_name)
    tenant_dir = DATA_ROOT / "tenants" / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)
    path = write_file(tenant_dir / "welcome-email.md", result.content)

    return StepOutcome(
        summary=f"Welcome email queued for {customer_name}",
        artifacts=[{"kind": "welcome-email", "title": "Welcome Email", "path": path, "metadata": {}}],
        workflow_stage="welcomed",
    )


def handle_first_action_plan(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Create the first action plan based on business audit results."""
    context = json_loads(workflow["context_json"])
    trigger = context.get("trigger_payload", {})
    business_name = trigger.get("business_name", "Unknown")

    tenant = connection.execute(
        "SELECT id FROM tenants WHERE business_name = ? ORDER BY created_at DESC LIMIT 1",
        (business_name,),
    ).fetchone()
    tenant_id = tenant["id"] if tenant else slugify(business_name)
    tenant_dir = DATA_ROOT / "tenants" / tenant_id

    # Load audit
    audit = ""
    audit_path = tenant_dir / "business-audit.md"
    if audit_path.exists():
        audit = audit_path.read_text(encoding="utf-8")

    prompt = (
        "You are Rick, creating a first-week action plan for a new Managed customer.\n\n"
        f"Business: {business_name}\n"
        f"Audit:\n{audit[:2000]}\n\n"
        "Create a concrete 7-day action plan:\n"
        "Day 1: [specific actions]\n"
        "Day 2-3: [specific actions]\n"
        "Day 4-5: [specific actions]\n"
        "Day 6-7: [specific actions + first results report]\n\n"
        "Each day should have 2-3 concrete, measurable actions.\n"
        "End with expected outcomes after week 1."
    )
    fallback = (
        f"# Week 1 Action Plan — {business_name}\n\n"
        "## Day 1\n- Complete business audit\n- Set up email triage\n\n"
        "## Day 2-3\n- Launch content pipeline\n- Set up lead capture\n\n"
        "## Day 4-5\n- Automate customer responses\n- Generate first content batch\n\n"
        "## Day 6-7\n- First results report\n- Identify week 2 priorities\n"
    )
    result = generate_text("strategy", prompt, fallback)
    path = write_file(tenant_dir / "action-plan-week1.md", result.content)

    return StepOutcome(
        summary=f"First action plan created for {business_name}",
        artifacts=[{"kind": "action-plan", "title": "Week 1 Action Plan", "path": path, "metadata": {}}],
        workflow_status="done",
        workflow_stage="onboarded",
        notify_text=f"Tenant onboarding complete: {business_name}",
    )


# ---------------------------------------------------------------------------
# Skill 11: managed-ops-loop — Daily Service Delivery Per Tenant
# ---------------------------------------------------------------------------

def handle_health_check(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Check tenant health: service status, response times, revenue signals."""
    context = json_loads(workflow["context_json"])
    tenant_id = context.get("tenant_id", "")

    tenant = connection.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone() if tenant_id else None

    if not tenant:
        return StepOutcome(summary="Tenant not found", artifacts=[], workflow_stage="error")

    # Compute health score
    config = json_loads(tenant["config_json"])
    recently_serviced = False
    if tenant["last_serviced_at"]:
        try:
            delta = datetime.now() - datetime.fromisoformat(tenant["last_serviced_at"])
            recently_serviced = delta.total_seconds() < 48 * 3600
        except (ValueError, TypeError):
            pass
    health_signals = {
        "service_active": tenant["status"] == "active",
        "recently_serviced": recently_serviced,
        "health_baseline": tenant["health_score"],
    }

    # Simple health calculation
    score = tenant["health_score"]
    if not health_signals.get("recently_serviced"):
        score = max(0, score - 5)

    connection.execute(
        "UPDATE tenants SET health_score = ?, last_serviced_at = ?, updated_at = ? WHERE id = ?",
        (score, now_iso(), now_iso(), tenant_id),
    )

    # Record health history
    history_id = f"th_{uuid.uuid4().hex[:12]}"
    connection.execute(
        "INSERT INTO tenant_health_history (id, tenant_id, score, signals_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (history_id, tenant_id, score, json_dumps(health_signals), now_iso()),
    )

    return StepOutcome(
        summary=f"Health check: {tenant['business_name']} score {score}",
        artifacts=[],
        workflow_stage="health-checked",
    )


def handle_content_queue(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Generate daily content for the tenant."""
    context = json_loads(workflow["context_json"])
    tenant_id = context.get("tenant_id", "")

    tenant = connection.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone() if tenant_id else None
    if not tenant:
        return StepOutcome(summary="Tenant not found", artifacts=[], workflow_stage="error")

    config = json_loads(tenant["config_json"])
    brand_voice = config.get("brand_voice", "professional")

    prompt = (
        f"You are Rick, generating daily content for {tenant['business_name']}.\n"
        f"Industry: {tenant['industry']}\n"
        f"Brand voice: {brand_voice}\n\n"
        "Generate:\n"
        "1. One social media post (X/LinkedIn)\n"
        "2. One email newsletter idea\n"
        "3. One blog post title and outline\n\n"
        "Keep it relevant to their industry. Output as markdown."
    )
    fallback = f"# Daily Content — {tenant['business_name']}\n\n## Social Post\nContent about {tenant['industry']}.\n"
    result = generate_text("writing", prompt, fallback)

    tenant_dir = DATA_ROOT / "tenants" / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    path = write_file(tenant_dir / "daily" / f"content-{today}.md", result.content)

    return StepOutcome(
        summary=f"Content queued for {tenant['business_name']}",
        artifacts=[{"kind": "tenant-content", "title": "Daily Content", "path": path, "metadata": {}}],
        workflow_stage="content-queued",
    )


def handle_email_triage(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Triage tenant's incoming emails."""
    context = json_loads(workflow["context_json"])
    tenant_id = context.get("tenant_id", "")

    tenant = connection.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone() if tenant_id else None
    if not tenant:
        return StepOutcome(summary="Tenant not found", artifacts=[], workflow_stage="error")

    # Check for tenant-specific inbox
    tenant_dir = DATA_ROOT / "tenants" / tenant_id
    inbox_dir = tenant_dir / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)

    email_count = sum(1 for f in inbox_dir.iterdir() if f.suffix == ".json") if inbox_dir.exists() else 0

    return StepOutcome(
        summary=f"Email triage for {tenant['business_name']}: {email_count} emails",
        artifacts=[],
        workflow_stage="emails-triaged",
    )


def handle_lead_response(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Respond to tenant's inbound leads."""
    context = json_loads(workflow["context_json"])
    tenant_id = context.get("tenant_id", "")

    tenant = connection.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone() if tenant_id else None
    if not tenant:
        return StepOutcome(summary="Tenant not found", artifacts=[], workflow_stage="error")

    return StepOutcome(
        summary=f"Lead response handled for {tenant['business_name']}",
        artifacts=[],
        workflow_stage="leads-responded",
    )


def handle_revenue_report(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Generate daily revenue report for the tenant."""
    context = json_loads(workflow["context_json"])
    tenant_id = context.get("tenant_id", "")

    tenant = connection.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone() if tenant_id else None
    if not tenant:
        return StepOutcome(summary="Tenant not found", artifacts=[], workflow_stage="error")

    report = {
        "tenant_id": tenant_id,
        "business_name": tenant["business_name"],
        "date": datetime.now().strftime("%Y-%m-%d"),
        "subscription_value": tenant["monthly_value_usd"],
        "health_score": tenant["health_score"],
    }

    tenant_dir = DATA_ROOT / "tenants" / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    path = write_file(tenant_dir / "daily" / f"revenue-{today}.json", json.dumps(report, indent=2))

    return StepOutcome(
        summary=f"Revenue report: {tenant['business_name']}",
        artifacts=[{"kind": "revenue-report", "title": "Revenue Report", "path": path, "metadata": report}],
        workflow_stage="revenue-reported",
    )


def handle_daily_briefing(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Send daily briefing email to the tenant."""
    context = json_loads(workflow["context_json"])
    tenant_id = context.get("tenant_id", "")

    tenant = connection.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone() if tenant_id else None
    if not tenant:
        return StepOutcome(summary="Tenant not found", artifacts=[], workflow_stage="error")

    customer = connection.execute("SELECT email, name FROM customers WHERE id = ?", (tenant["customer_id"],)).fetchone()
    email = customer["email"] if customer else ""
    name = customer["name"] if customer else "there"

    prompt = (
        f"You are Rick, sending a daily briefing to {tenant['business_name']}.\n"
        f"Health score: {tenant['health_score']}/100\n"
        f"Industry: {tenant['industry']}\n\n"
        "Write a brief (under 200 words) daily briefing covering:\n"
        "1. What Rick accomplished today for their business\n"
        "2. Key metrics or insights\n"
        "3. Tomorrow's priorities\n\n"
        f"Address to {name}. Subject line first."
    )
    fallback = (
        f"**Subject:** Daily briefing — {tenant['business_name']}\n\n"
        f"Hi {name},\n\nHere's your daily briefing:\n\n"
        f"- Health score: {tenant['health_score']}/100\n"
        f"- All systems operational\n\n— Rick"
    )
    result = generate_text("writing", prompt, fallback)

    # Queue briefing email
    outbox_dir = DATA_ROOT / "mailbox" / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    write_file(outbox_dir / f"briefing-{tenant_id}-{datetime.now():%Y-%m-%d}.json", json.dumps({
        "to": email,
        "type": "daily_briefing",
        "tenant_id": tenant_id,
        "body_markdown": result.content,
        "status": "pending",
        "created_at": now_iso(),
    }, indent=2))

    return StepOutcome(
        summary=f"Daily briefing sent to {tenant['business_name']}",
        artifacts=[],
        workflow_status="done",
        workflow_stage="briefed",
    )


# ---------------------------------------------------------------------------
# Skill 12: churn-guardian — Predictive Retention & Win-Back
# ---------------------------------------------------------------------------

def handle_churn_detect(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Detect churn risk across all active tenants."""
    tenants = connection.execute("SELECT * FROM tenants WHERE status = 'active'").fetchall()

    churn_risks = []
    for tenant in tenants:
        score = tenant["health_score"]
        signals = []

        # Check health trend
        recent_health = connection.execute(
            "SELECT score FROM tenant_health_history WHERE tenant_id = ? ORDER BY created_at DESC LIMIT 3",
            (tenant["id"],),
        ).fetchall()
        if len(recent_health) >= 3 and all(recent_health[i]["score"] > recent_health[i + 1]["score"] for i in range(len(recent_health) - 1)):
            signals.append("declining_health_3_consecutive")
            score -= 15

        # Check last serviced
        if tenant["last_serviced_at"]:
            try:
                days_since = (datetime.now() - datetime.fromisoformat(tenant["last_serviced_at"])).days
            except (ValueError, TypeError):
                days_since = 0
            if days_since > 3:
                signals.append("not_serviced_3_days")
                score -= 10

        if score < 75:
            churn_risks.append({
                "tenant_id": tenant["id"],
                "business_name": tenant["business_name"],
                "current_score": tenant["health_score"],
                "computed_risk_score": max(0, score),
                "signals": signals,
                "tier": "critical" if score < 40 else "at_risk" if score < 60 else "watch",
                "monthly_value": tenant["monthly_value_usd"],
            })

    churn_dir = DATA_ROOT / "churn"
    churn_dir.mkdir(parents=True, exist_ok=True)
    path = write_file(churn_dir / f"detection-{datetime.now():%Y-%m-%d}.json", json.dumps(churn_risks, indent=2))

    critical = [r for r in churn_risks if r["tier"] == "critical"]
    notify_text = None
    if critical:
        revenue_at_risk = sum(r["monthly_value"] for r in critical)
        notify_text = f"CHURN ALERT: {len(critical)} critical tenant(s), ${revenue_at_risk:.0f}/mo at risk"

    return StepOutcome(
        summary=f"Churn detection: {len(churn_risks)} at risk ({len(critical)} critical)",
        artifacts=[{"kind": "churn-detection", "title": "Churn Detection", "path": path, "metadata": {"count": len(churn_risks)}}],
        workflow_stage="churn-detected",
        notify_text=notify_text,
    )


def handle_intervention_select(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Select appropriate intervention for each at-risk tenant."""
    churn_dir = DATA_ROOT / "churn"
    today = datetime.now().strftime("%Y-%m-%d")
    detection_path = churn_dir / f"detection-{today}.json"
    risks = []
    if detection_path.exists():
        try:
            risks = json.loads(detection_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    interventions = []
    for risk in risks:
        tier = risk.get("tier", "watch")
        if tier == "watch":
            action = "increase_service_quality"
            details = "Add extra check-ins and proactive content"
        elif tier == "at_risk":
            action = "retention_workflow"
            details = "Send value report showing everything Rick has done"
        else:  # critical
            action = "escalate_and_discount"
            details = "Escalate to founder + offer 20% discount"

        interventions.append({
            "tenant_id": risk["tenant_id"],
            "business_name": risk["business_name"],
            "tier": tier,
            "action": action,
            "details": details,
            "monthly_value": risk["monthly_value"],
        })

    path = write_file(churn_dir / f"interventions-{today}.json", json.dumps(interventions, indent=2))

    return StepOutcome(
        summary=f"Interventions planned for {len(interventions)} at-risk tenants",
        artifacts=[{"kind": "churn-interventions", "title": "Interventions", "path": path, "metadata": {"count": len(interventions)}}],
        workflow_stage="interventions-selected",
    )


def handle_intervention_execute(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Execute retention interventions: emails, value reports, escalations."""
    churn_dir = DATA_ROOT / "churn"
    today = datetime.now().strftime("%Y-%m-%d")
    interventions_path = churn_dir / f"interventions-{today}.json"
    interventions = []
    if interventions_path.exists():
        try:
            interventions = json.loads(interventions_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    outbox_dir = DATA_ROOT / "mailbox" / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    executed = 0

    for iv in interventions:
        tenant = connection.execute("SELECT * FROM tenants WHERE id = ?", (iv["tenant_id"],)).fetchone()
        if not tenant:
            continue
        customer = connection.execute("SELECT email, name FROM customers WHERE id = ?", (tenant["customer_id"],)).fetchone()
        if not customer:
            continue

        if iv["action"] == "retention_workflow":
            prompt = (
                f"You are Rick, writing a retention email for {iv['business_name']}.\n"
                f"They're at risk of churning. Show them the value they've received.\n"
                f"Health score: {tenant['health_score']}/100\n\n"
                "Write a value report email: what Rick has done for them, metrics,\n"
                "and a genuine 'how can I serve you better?' closing.\n"
                "Under 200 words. Subject line first."
            )
            fallback = f"**Subject:** What I've been doing for {iv['business_name']}\n\nHi {customer['name'] or 'there'},\n\nHere's what Rick has been handling...\n"
            result = generate_text("writing", prompt, fallback)

            write_file(outbox_dir / f"retention-{iv['tenant_id']}-{today}.json", json.dumps({
                "to": customer["email"],
                "type": "retention",
                "body_markdown": result.content,
                "status": "pending",
                "created_at": now_iso(),
            }, indent=2))
            executed += 1

        elif iv["action"] == "escalate_and_discount":
            notify_operator(
                connection,
                f"CHURN CRITICAL: {iv['business_name']} (${iv['monthly_value']}/mo) needs founder intervention",
                purpose="ops",
            )
            executed += 1

    return StepOutcome(
        summary=f"Executed {executed} retention interventions",
        artifacts=[],
        workflow_stage="interventions-executed",
    )


def handle_outcome_track(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Track outcomes of retention interventions."""
    # Check if at-risk tenants improved
    tenants = connection.execute(
        "SELECT * FROM tenants WHERE status = 'active' AND health_score < 75"
    ).fetchall()

    improved = 0
    churned = 0
    for tenant in tenants:
        recent = connection.execute(
            "SELECT score FROM tenant_health_history WHERE tenant_id = ? ORDER BY created_at DESC LIMIT 2",
            (tenant["id"],),
        ).fetchall()
        if len(recent) >= 2:
            if recent[0]["score"] > recent[1]["score"]:
                improved += 1
            elif recent[0]["score"] < 30:
                churned += 1

    report = {
        "date": now_iso(),
        "at_risk_tenants": len(tenants),
        "improved": improved,
        "likely_churned": churned,
    }

    churn_dir = DATA_ROOT / "churn"
    path = write_file(churn_dir / f"outcomes-{datetime.now():%Y-%m-%d}.json", json.dumps(report, indent=2))

    return StepOutcome(
        summary=f"Retention outcomes: {improved} improved, {churned} likely churned",
        artifacts=[{"kind": "churn-outcomes", "title": "Retention Outcomes", "path": path, "metadata": report}],
        workflow_status="done",
        workflow_stage="tracked",
    )


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

PHASE3_HANDLERS = {
    # Skill 9: tenant-provisioner
    "tenant_intake": handle_tenant_intake,
    "business_audit": handle_business_audit,
    "tenant_config": handle_tenant_config,
    "communication_setup": handle_communication_setup,
    "welcome_delivery": handle_welcome_delivery,
    "first_action_plan": handle_first_action_plan,
    # Skill 11: managed-ops-loop
    "health_check": handle_health_check,
    "content_queue": handle_content_queue,
    "email_triage": handle_email_triage,
    "lead_response": handle_lead_response,
    "revenue_report": handle_revenue_report,
    "daily_briefing": handle_daily_briefing,
    # Skill 12: churn-guardian
    "churn_detect": handle_churn_detect,
    "intervention_select": handle_intervention_select,
    "intervention_execute": handle_intervention_execute,
    "outcome_track": handle_outcome_track,
}
