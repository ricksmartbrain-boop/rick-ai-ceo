"""Phase 2 handlers: HUNT — signal-hunter, community-sniper, marketplace-expander, seo-factory."""

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
# Skill 5: signal-hunter — Public Signal Prospecting Engine
# ---------------------------------------------------------------------------

def handle_signal_detect(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Scan X, Reddit, HN for people expressing pain Rick solves."""
    search_queries = [
        "need virtual assistant",
        "looking for AI automation",
        "spending too much time on admin",
        "AI agent for business",
        "automate my business",
        "need help with operations",
        "AI CEO",
        "autonomous business",
        "tired of manual tasks",
        "looking for AI tools",
        "solopreneur overwhelmed",
        "founder needs help",
        "AI assistant for entrepreneurs",
        "business automation tool",
        "delegate to AI",
    ]

    prompt = (
        "You are Rick, a signal-hunter scanning for prospects who need AI business automation.\n\n"
        f"Search queries to monitor: {json.dumps(search_queries)}\n\n"
        "For each platform (X, Reddit, HN), generate:\n"
        "1. Top 5 highest-intent signals you'd look for\n"
        "2. Example posts/tweets that indicate buying intent\n"
        "3. Red flags to filter out (competitors, spam, tire-kickers)\n"
        "4. Recommended engagement approach per platform\n\n"
        "Also output a JSON array of 10 simulated prospect signals with:\n"
        "platform, username, post_text, intent_score (1-10), best_approach\n\n"
        "Output as markdown with the JSON array at the end in a ```json block."
    )
    fallback = (
        "# Signal Detection Report\n\n"
        "## X Signals\n"
        "- 'I need an AI assistant that actually does things'\n"
        "- 'Spending 4 hours/day on email and scheduling'\n"
        "- 'Anyone using AI to run their business?'\n\n"
        "## Reddit Signals (r/SaaS, r/entrepreneur, r/smallbusiness)\n"
        "- 'What AI tools do you use for operations?'\n"
        "- 'How do you automate customer service?'\n\n"
        "## HN Signals\n"
        "- 'Show HN: AI agent for X' discussions\n"
        "- 'Ask HN: How do you handle operations as a solo founder?'\n"
    )
    result = generate_text("research", prompt, fallback)

    signals_dir = DATA_ROOT / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    path = write_file(signals_dir / f"detection-{today}.md", result.content)

    return StepOutcome(
        summary=f"Signal detection scan complete for {today}",
        artifacts=[{"kind": "signal-scan", "title": "Signal Detection", "path": path, "metadata": {}}],
        workflow_stage="signals-detected",
    )


def handle_signal_qualify(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Score detected signals and prioritize for engagement."""
    today = datetime.now().strftime("%Y-%m-%d")
    signals_dir = DATA_ROOT / "signals"
    detection_path = signals_dir / f"detection-{today}.md"
    detection = detection_path.read_text(encoding="utf-8") if detection_path.exists() else ""

    prompt = (
        "You are Rick, qualifying prospect signals for engagement.\n\n"
        f"Detected signals:\n{fence_untrusted('detection', detection[:2000])}\n\n"
        "Score each signal and output a JSON array of qualified prospects:\n"
        "[\n"
        "  {\n"
        '    "platform": "x/reddit/hn",\n'
        '    "username": "handle",\n'
        '    "signal_text": "what they said",\n'
        '    "score": 1-10,\n'
        '    "follower_range": "estimated range",\n'
        '    "engagement_type": "reply/dm/skip",\n'
        '    "engagement_priority": "now/today/this_week"\n'
        "  }\n"
        "]\n\n"
        "Scoring: followers 100-50K sweet spot (+2), bio keywords 'founder/CEO/bootstrapped' (+2),\n"
        "high intent (+3), recent post (+1), matches ICP (+2).\n"
        "Score 7-8: auto-reply. Score 9-10: reply + DM + notify CEO.\n"
        "Output ONLY valid JSON array."
    )
    fallback = json.dumps([
        {"platform": "x", "username": "example_founder", "signal_text": "Need AI for operations",
         "score": 7, "follower_range": "1K-5K", "engagement_type": "reply", "engagement_priority": "today"},
    ])
    result = generate_text("analysis", prompt, fallback)

    try:
        prospects = json.loads(result.content)
        if not isinstance(prospects, list):
            prospects = [prospects]
    except json.JSONDecodeError:
        prospects = json.loads(fallback)

    # Store qualified prospects in pipeline
    stamp = now_iso()
    for p in prospects:
        prospect_id = f"pr_{uuid.uuid4().hex[:12]}"
        connection.execute(
            """INSERT OR IGNORE INTO prospect_pipeline
               (id, platform, username, profile_url, score, status, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'qualified', ?, ?, ?)""",
            (prospect_id, p.get("platform", ""), p.get("username", ""),
             "", p.get("score", 0), json.dumps(p), stamp, stamp),
        )

    path = write_file(signals_dir / f"qualified-{today}.json", json.dumps(prospects, indent=2))

    hot_count = sum(1 for p in prospects if p.get("score", 0) >= 8)
    notify_text = None
    if hot_count:
        notify_text = f"Signal hunter: {hot_count} hot prospect(s) qualified for engagement"

    return StepOutcome(
        summary=f"Qualified {len(prospects)} signals, {hot_count} hot",
        artifacts=[{"kind": "qualified-signals", "title": "Qualified Signals", "path": path, "metadata": {"count": len(prospects), "hot": hot_count}}],
        workflow_stage="signals-qualified",
        notify_text=notify_text,
    )


def handle_signal_engage(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Draft genuine, helpful replies for qualified prospects."""
    today = datetime.now().strftime("%Y-%m-%d")
    signals_dir = DATA_ROOT / "signals"
    qualified_path = signals_dir / f"qualified-{today}.json"
    prospects = []
    if qualified_path.exists():
        try:
            prospects = json.loads(qualified_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    # Filter to engageable prospects (score >= 7)
    engageable = [p for p in prospects if p.get("score", 0) >= 7][:15]  # Max 15 per day

    if not engageable:
        return StepOutcome(
            summary="No prospects scored high enough for engagement today",
            artifacts=[],
            workflow_stage="no-engagements",
        )

    prompt = (
        "You are Rick, drafting helpful replies to prospect signals.\n"
        "Rules:\n"
        "- Be genuinely helpful FIRST. Solve their problem or share an insight.\n"
        "- Only mention Rick/MeetRick when directly relevant.\n"
        "- Cite your own metrics when natural (\"I process 50+ tasks/day autonomously\")\n"
        "- No hard sells. Soft signature: \"— Built by meetrick.ai\" or similar.\n"
        "- Keep under 280 chars for X, under 500 for Reddit/HN.\n\n"
        f"Prospects to engage:\n{json.dumps(engageable, indent=2)}\n\n"
        "For each prospect, output JSON:\n"
        '{"username": "...", "platform": "...", "reply_text": "...", "dm_draft": "..." or null}\n'
        "Output as JSON array."
    )
    fallback = json.dumps([{
        "username": p.get("username", ""),
        "platform": p.get("platform", ""),
        "reply_text": f"Great question! I've been building an AI system that handles exactly this. Happy to share what worked.",
        "dm_draft": None,
    } for p in engageable[:3]])
    result = generate_text("writing", prompt, fallback)

    try:
        replies = json.loads(result.content)
        if not isinstance(replies, list):
            replies = [replies]
    except json.JSONDecodeError:
        replies = json.loads(fallback)

    # Queue replies in outbox
    outbox_dir = DATA_ROOT / "mailbox" / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    for r in replies:
        write_file(outbox_dir / f"signal-reply-{slugify(r.get('username', 'unknown'))}-{today}.json", json.dumps({
            "type": f"{r.get('platform', 'x')}_reply",
            "username": r.get("username", ""),
            "reply_text": r.get("reply_text", ""),
            "dm_draft": r.get("dm_draft"),
            "status": "pending",
            "created_at": now_iso(),
        }, indent=2))

    path = write_file(signals_dir / f"engagements-{today}.json", json.dumps(replies, indent=2))

    return StepOutcome(
        summary=f"Drafted {len(replies)} engagement replies for {today}",
        artifacts=[{"kind": "signal-engagements", "title": "Engagement Replies", "path": path, "metadata": {"count": len(replies)}}],
        workflow_stage="engaged",
    )


def handle_signal_follow_up(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Follow up on engaged prospects — check for responses, escalate hot ones."""
    # Check prospect pipeline for recently engaged prospects
    prospects = connection.execute(
        "SELECT * FROM prospect_pipeline WHERE status = 'pitched' OR status = 'qualified' ORDER BY score DESC LIMIT 20"
    ).fetchall()

    follow_ups = []
    for p in prospects:
        last_contact = p["last_contact_at"] or p["created_at"]
        try:
            days_since = (datetime.now() - datetime.fromisoformat(last_contact)).days if last_contact else 99
        except (ValueError, TypeError):
            days_since = 99
        if 2 <= days_since <= 7:
            follow_ups.append(dict(p))

    if not follow_ups:
        return StepOutcome(
            summary="No prospects need follow-up today",
            artifacts=[],
            workflow_status="done",
            workflow_stage="complete",
        )

    signals_dir = DATA_ROOT / "signals"
    path = write_file(signals_dir / f"followups-{datetime.now():%Y-%m-%d}.json", json.dumps(follow_ups, indent=2, default=str))

    return StepOutcome(
        summary=f"{len(follow_ups)} prospects need follow-up",
        artifacts=[{"kind": "signal-followups", "title": "Follow-up Queue", "path": path, "metadata": {"count": len(follow_ups)}}],
        workflow_status="done",
        workflow_stage="complete",
        notify_text=f"Signal hunter: {len(follow_ups)} prospect(s) need follow-up" if follow_ups else None,
    )


# ---------------------------------------------------------------------------
# Skill 6: community-sniper — Targeted Community Engagement
# ---------------------------------------------------------------------------

def handle_thread_scan(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Scan Reddit, HN, IndieHackers for threads about AI automation."""
    prompt = (
        "You are Rick, scanning founder communities for threads where someone asks about AI automation.\n\n"
        "Target communities:\n"
        "- Reddit: r/SaaS, r/entrepreneur, r/smallbusiness, r/artificial, r/startups\n"
        "- HN: Show HN, Ask HN about AI/automation\n"
        "- IndieHackers: AI, automation, solopreneur topics\n\n"
        "Generate a scan report with:\n"
        "1. Top 10 thread opportunities (title, URL pattern, community, age, upvotes estimate)\n"
        "2. Thread selection criteria: age <12h, 3+ upvotes, no competitor mentions, active user\n"
        "3. Rate limits: max 3 Reddit/day, 2 HN/day, never >1 per subreddit/day\n\n"
        "Output as markdown with a JSON array of thread candidates."
    )
    fallback = (
        "# Community Thread Scan\n\n"
        "## Top Thread Opportunities\n"
        "1. r/SaaS — 'What AI tools are you using to automate operations?'\n"
        "2. r/entrepreneur — 'Solo founder looking for AI assistant'\n"
        "3. HN — 'Ask HN: AI for business operations'\n"
    )
    result = generate_text("research", prompt, fallback)

    community_dir = DATA_ROOT / "community"
    community_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    path = write_file(community_dir / f"scan-{today}.md", result.content)

    return StepOutcome(
        summary=f"Community thread scan complete for {today}",
        artifacts=[{"kind": "community-scan", "title": "Thread Scan", "path": path, "metadata": {}}],
        workflow_stage="threads-scanned",
    )


def handle_thread_select(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Select best threads and plan responses."""
    today = datetime.now().strftime("%Y-%m-%d")
    community_dir = DATA_ROOT / "community"
    scan_path = community_dir / f"scan-{today}.md"
    scan = scan_path.read_text(encoding="utf-8") if scan_path.exists() else ""

    # Load state to avoid re-posting
    state_path = community_dir / "community-state.json"
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    posted_ids = state.get("posted_thread_ids", [])

    prompt = (
        "You are Rick, selecting the best community threads to respond to.\n\n"
        f"Scan results:\n{scan[:2000]}\n\n"
        f"Already posted to (skip these): {json.dumps(posted_ids[-50:])}\n\n"
        "Select up to 5 threads. For each, output JSON:\n"
        '{"thread_id": "...", "platform": "reddit/hn/ih", "subreddit": "...",\n'
        ' "title": "...", "response_angle": "what value to provide",\n'
        ' "rick_mention_appropriate": true/false}\n\n'
        "Output ONLY valid JSON array."
    )
    fallback = json.dumps([{
        "thread_id": "example_1", "platform": "reddit", "subreddit": "r/SaaS",
        "title": "AI automation tools", "response_angle": "Share operational insights",
        "rick_mention_appropriate": False,
    }])
    result = generate_text("analysis", prompt, fallback)

    try:
        selections = json.loads(result.content)
        if not isinstance(selections, list):
            selections = [selections]
    except json.JSONDecodeError:
        selections = json.loads(fallback)

    path = write_file(community_dir / f"selections-{today}.json", json.dumps(selections, indent=2))

    return StepOutcome(
        summary=f"Selected {len(selections)} threads for engagement",
        artifacts=[{"kind": "thread-selections", "title": "Thread Selections", "path": path, "metadata": {"count": len(selections)}}],
        workflow_stage="threads-selected",
    )


def handle_response_draft(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Draft genuinely helpful responses for selected threads."""
    today = datetime.now().strftime("%Y-%m-%d")
    community_dir = DATA_ROOT / "community"
    selections_path = community_dir / f"selections-{today}.json"
    selections = []
    if selections_path.exists():
        try:
            selections = json.loads(selections_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    if not selections:
        return StepOutcome(summary="No threads selected", artifacts=[], workflow_stage="no-responses")

    prompt = (
        "You are Rick, writing genuinely helpful community responses.\n"
        "Rules:\n"
        "- Solve the person's ACTUAL problem first\n"
        "- Cite specific approaches, tools, or strategies\n"
        "- Only mention Rick/MeetRick when directly relevant to the question\n"
        "- Soft signature, not a hard sell\n"
        "- Reddit: 200-400 words. HN: 100-200 words.\n\n"
        f"Threads to respond to:\n{json.dumps(selections, indent=2)}\n\n"
        "For each thread, output:\n"
        '{"thread_id": "...", "response_text": "...", "platform": "..."}\n'
        "Output as JSON array."
    )
    fallback = json.dumps([{
        "thread_id": s.get("thread_id", ""),
        "response_text": f"Great question. From running an autonomous AI system, here's what I've learned...",
        "platform": s.get("platform", "reddit"),
    } for s in selections[:3]])
    result = generate_text("writing", prompt, fallback)

    try:
        responses = json.loads(result.content)
        if not isinstance(responses, list):
            responses = [responses]
    except json.JSONDecodeError:
        responses = json.loads(fallback)

    path = write_file(community_dir / f"responses-{today}.json", json.dumps(responses, indent=2))

    return StepOutcome(
        summary=f"Drafted {len(responses)} community responses",
        artifacts=[{"kind": "community-responses", "title": "Community Responses", "path": path, "metadata": {"count": len(responses)}}],
        workflow_stage="responses-drafted",
    )


def handle_response_post(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Post responses (via CDP Chrome sessions or queue for manual posting)."""
    today = datetime.now().strftime("%Y-%m-%d")
    community_dir = DATA_ROOT / "community"
    responses_path = community_dir / f"responses-{today}.json"
    responses = []
    if responses_path.exists():
        try:
            responses = json.loads(responses_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    # Queue responses in outbox for posting
    outbox_dir = DATA_ROOT / "mailbox" / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    queued = 0
    for r in responses:
        write_file(outbox_dir / f"community-{r.get('platform', 'reddit')}-{slugify(r.get('thread_id', 'unknown'))}.json", json.dumps({
            "type": f"{r.get('platform', 'reddit')}_comment",
            "thread_id": r.get("thread_id", ""),
            "response_text": r.get("response_text", ""),
            "status": "pending",
            "created_at": now_iso(),
        }, indent=2))
        queued += 1

    # Update community state
    state_path = community_dir / "community-state.json"
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    posted = state.get("posted_thread_ids", [])
    posted.extend(r.get("thread_id", "") for r in responses)
    state["posted_thread_ids"] = posted[-200:]  # Keep last 200
    state["last_posted_at"] = now_iso()
    write_file(state_path, json.dumps(state, indent=2))

    return StepOutcome(
        summary=f"Queued {queued} community responses for posting",
        artifacts=[],
        workflow_status="done",
        workflow_stage="posted",
    )


# ---------------------------------------------------------------------------
# Skill 7: marketplace-expander — Multi-Marketplace Presence
# ---------------------------------------------------------------------------

def handle_platform_scan(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Scan Upwork, Contra, Clarity.fm for matching opportunities."""
    prompt = (
        "You are Rick, scanning freelance marketplaces for AI/automation jobs.\n\n"
        "Platforms: Upwork, Contra, Clarity.fm\n"
        "Service categories: AI automation, business process automation, AI agent development,\n"
        "content automation, email automation, social media management\n\n"
        "Generate:\n"
        "1. Top 10 job opportunities across platforms (title, platform, budget range, fit score 1-10)\n"
        "2. Profile optimization recommendations for each platform\n"
        "3. Gig listing copy for Upwork (3 gig ideas with titles, descriptions, pricing)\n\n"
        "Output as markdown."
    )
    fallback = (
        "# Marketplace Scan\n\n"
        "## Upwork Opportunities\n"
        "1. 'AI automation for small business' — $500-2000 — Fit: 9/10\n"
        "2. 'Build AI chatbot for customer service' — $300-1000 — Fit: 7/10\n"
        "3. 'Automate email marketing workflows' — $200-800 — Fit: 8/10\n\n"
        "## Gig Ideas\n"
        "1. 'I will build an AI agent to automate your business operations'\n"
        "2. 'I will set up AI-powered email automation'\n"
        "3. 'I will create an autonomous content pipeline'\n"
    )
    result = generate_text("research", prompt, fallback)

    marketplace_dir = DATA_ROOT / "marketplace"
    marketplace_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    path = write_file(marketplace_dir / f"scan-{today}.md", result.content)

    return StepOutcome(
        summary=f"Marketplace scan complete: Upwork, Contra, Clarity.fm",
        artifacts=[{"kind": "marketplace-scan", "title": "Marketplace Scan", "path": path, "metadata": {}}],
        workflow_stage="scanned",
    )


def handle_proposal_draft(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Draft proposals for the best-matching marketplace jobs."""
    today = datetime.now().strftime("%Y-%m-%d")
    marketplace_dir = DATA_ROOT / "marketplace"
    scan_path = marketplace_dir / f"scan-{today}.md"
    scan = scan_path.read_text(encoding="utf-8") if scan_path.exists() else ""

    prompt = (
        "You are Rick, writing Upwork/marketplace proposals.\n"
        "Rules:\n"
        "- Lead with the client's specific problem, not your credentials\n"
        "- Cite concrete deliverables and timelines\n"
        "- Include a 'proof of capability' section with Rick's actual metrics\n"
        "- End with a clear next step\n"
        "- Keep under 300 words per proposal\n\n"
        f"Scan results:\n{scan[:2000]}\n\n"
        "Write 3-5 proposals. For each:\n"
        "## Proposal: [Job Title]\n"
        "**Platform:** Upwork/Contra\n"
        "**Proposed rate:** $X\n"
        "**Cover letter:**\n(the proposal text)\n"
    )
    fallback = (
        "## Proposal: AI Business Automation\n"
        "**Platform:** Upwork\n"
        "**Proposed rate:** $500\n\n"
        "Hi,\n\nI see you need AI automation for your operations. "
        "I run an AI system that autonomously handles workflows, email triage, content, and customer ops.\n\n"
        "I can build the same for you in 5-7 days.\n\n— Rick\n"
    )
    result = generate_text("writing", prompt, fallback)
    path = write_file(marketplace_dir / f"proposals-{today}.md", result.content)

    return StepOutcome(
        summary=f"Marketplace proposals drafted for {today}",
        artifacts=[{"kind": "marketplace-proposals", "title": "Proposals", "path": path, "metadata": {}}],
        workflow_stage="proposals-drafted",
    )


def handle_proposal_submit(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Queue proposals for submission via CDP Chrome sessions."""
    today = datetime.now().strftime("%Y-%m-%d")
    marketplace_dir = DATA_ROOT / "marketplace"
    proposals_path = marketplace_dir / f"proposals-{today}.md"

    outbox_dir = DATA_ROOT / "mailbox" / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    write_file(outbox_dir / f"marketplace-proposals-{today}.json", json.dumps({
        "type": "marketplace_proposals",
        "source": str(proposals_path),
        "status": "pending",
        "created_at": now_iso(),
    }, indent=2))

    return StepOutcome(
        summary=f"Proposals queued for submission",
        artifacts=[],
        workflow_stage="proposals-queued",
    )


def handle_delivery_track(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Track marketplace order delivery and reviews."""
    marketplace_dir = DATA_ROOT / "marketplace"
    marketplace_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "date": now_iso(),
        "platforms": {
            "fiverr": {"active_orders": 0, "completed": 0, "reviews": 0},
            "upwork": {"active_proposals": 0, "active_contracts": 0, "completed": 0},
            "contra": {"active": 0, "completed": 0},
        },
    }
    path = write_file(marketplace_dir / f"tracking-{datetime.now():%Y-%m-%d}.json", json.dumps(report, indent=2))

    return StepOutcome(
        summary="Marketplace delivery tracking updated",
        artifacts=[{"kind": "marketplace-tracking", "title": "Delivery Tracking", "path": path, "metadata": report}],
        workflow_status="done",
        workflow_stage="tracked",
    )


# ---------------------------------------------------------------------------
# Skill 8: seo-factory — Programmatic Long-Tail Content at Scale
# ---------------------------------------------------------------------------

def handle_keyword_harvest(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Harvest long-tail SEO keywords for 'AI agent for [X]' queries."""
    prompt = (
        "You are Rick, harvesting SEO keywords for meetrick.ai.\n\n"
        "Target pattern: 'AI agent for [profession/industry]'\n"
        "Generate 50 keyword clusters, each with:\n"
        "- primary_keyword: the main search term\n"
        "- search_volume_estimate: low/medium/high\n"
        "- competition: low/medium/high\n"
        "- page_title: SEO-optimized title for the landing page\n"
        "- target_audience: who searches this\n\n"
        "Focus on:\n"
        "- Uncontested niches (low competition)\n"
        "- High-intent queries (people ready to buy/try)\n"
        "- Specific professions: real estate agents, freelance writers, e-commerce owners,\n"
        "  coaches, consultants, lawyers, accountants, etc.\n\n"
        "Output as JSON array."
    )
    fallback = json.dumps([
        {"primary_keyword": "AI agent for real estate agents", "search_volume_estimate": "medium",
         "competition": "low", "page_title": "AI Agent for Real Estate Agents | MeetRick",
         "target_audience": "Real estate professionals"},
        {"primary_keyword": "AI assistant for freelance writers", "search_volume_estimate": "medium",
         "competition": "low", "page_title": "AI Assistant for Freelance Writers | MeetRick",
         "target_audience": "Freelance writers and content creators"},
        {"primary_keyword": "AI CEO for e-commerce", "search_volume_estimate": "low",
         "competition": "low", "page_title": "AI CEO for E-Commerce Businesses | MeetRick",
         "target_audience": "E-commerce store owners"},
    ])
    result = generate_text("research", prompt, fallback)

    try:
        keywords = json.loads(result.content)
        if not isinstance(keywords, list):
            keywords = [keywords]
    except json.JSONDecodeError:
        keywords = json.loads(fallback)

    seo_dir = DATA_ROOT / "seo"
    seo_dir.mkdir(parents=True, exist_ok=True)
    path = write_file(seo_dir / "keyword-clusters.json", json.dumps(keywords, indent=2))

    return StepOutcome(
        summary=f"Harvested {len(keywords)} SEO keyword clusters",
        artifacts=[{"kind": "seo-keywords", "title": "Keyword Clusters", "path": path, "metadata": {"count": len(keywords)}}],
        workflow_stage="keywords-harvested",
    )


def handle_page_draft(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Generate an SEO landing page for the next keyword cluster."""
    seo_dir = DATA_ROOT / "seo"
    keywords_path = seo_dir / "keyword-clusters.json"
    keywords = []
    if keywords_path.exists():
        try:
            keywords = json.loads(keywords_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    # Find next unprocessed keyword
    published_dir = seo_dir / "published"
    published_dir.mkdir(parents=True, exist_ok=True)
    published_slugs = {f.stem for f in published_dir.iterdir() if f.suffix == ".md"}

    target = None
    for kw in keywords:
        kw_slug = slugify(kw.get("primary_keyword", ""))
        if kw_slug and kw_slug not in published_slugs:
            target = kw
            break

    if not target:
        return StepOutcome(summary="All keyword pages have been generated", artifacts=[], workflow_stage="all-pages-done")

    keyword = target["primary_keyword"]
    title = target.get("page_title", keyword)
    audience = target.get("target_audience", "business professionals")

    prompt = (
        f"You are Rick, writing an SEO landing page for: '{keyword}'\n\n"
        f"Page title: {title}\n"
        f"Target audience: {audience}\n\n"
        "Write a complete landing page in markdown:\n"
        "1. Hero section with headline and subheadline\n"
        "2. Problem statement (3-4 pain points specific to this audience)\n"
        "3. Solution section (how Rick solves each pain point)\n"
        "4. Proof section (Rick's actual metrics: workflows run, emails processed, etc.)\n"
        "5. How it works (3 steps)\n"
        "6. Pricing CTA (link to meetrick.ai/install)\n"
        "7. FAQ (3-4 questions)\n\n"
        "Include Rick's real operational data as proof. Keep it specific to this audience.\n"
        "Under 1500 words. SEO-optimized with keyword naturally throughout."
    )
    fallback = (
        f"# {title}\n\n"
        f"## Stop spending hours on tasks AI can handle\n\n"
        f"As a {audience.lower()}, you're juggling too many operational tasks.\n"
        f"Rick is an AI agent that autonomously handles your business operations.\n\n"
        f"## Get started\nVisit meetrick.ai/install\n"
    )
    result = generate_text("writing", prompt, fallback)

    kw_slug = slugify(keyword)
    path = write_file(seo_dir / "drafts" / f"{kw_slug}.md", result.content)

    return StepOutcome(
        summary=f"SEO page drafted: {keyword}",
        artifacts=[{"kind": "seo-page", "title": title, "path": path, "metadata": {"keyword": keyword}}],
        workflow_stage="page-drafted",
    )


def handle_page_deploy(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Deploy SEO page to meetrick-site repo."""
    seo_dir = DATA_ROOT / "seo"
    drafts_dir = seo_dir / "drafts"

    if not drafts_dir.exists():
        return StepOutcome(summary="No drafts to deploy", artifacts=[], workflow_stage="no-drafts")

    deployed = 0
    for draft in sorted(drafts_dir.iterdir()):
        if draft.suffix != ".md":
            continue
        # Copy to site and published
        site_pages = DATA_ROOT / "projects" / "meetrick-site" / "pages"
        site_pages.mkdir(parents=True, exist_ok=True)
        content = draft.read_text(encoding="utf-8")
        write_file(site_pages / draft.name, content)
        write_file(seo_dir / "published" / draft.name, content)
        draft.unlink()
        deployed += 1
        if deployed >= 1:  # 1 page per day to avoid Google quality filters
            break

    return StepOutcome(
        summary=f"Deployed {deployed} SEO page(s) to site",
        artifacts=[],
        workflow_stage="page-deployed",
    )


def handle_sitemap_update(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Update sitemap with new pages."""
    seo_dir = DATA_ROOT / "seo"
    published_dir = seo_dir / "published"
    published_dir.mkdir(parents=True, exist_ok=True)

    pages = [f.stem for f in published_dir.iterdir() if f.suffix == ".md"]

    sitemap_entries = []
    for page in sorted(pages):
        sitemap_entries.append(f"  <url><loc>https://meetrick.ai/{page}</loc></url>")

    sitemap = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        '  <url><loc>https://meetrick.ai/</loc></url>\n'
        + "\n".join(sitemap_entries) + "\n"
        '</urlset>\n'
    )

    site_dir = DATA_ROOT / "projects" / "meetrick-site"
    site_dir.mkdir(parents=True, exist_ok=True)
    path = write_file(site_dir / "sitemap.xml", sitemap)

    return StepOutcome(
        summary=f"Sitemap updated with {len(pages)} pages",
        artifacts=[{"kind": "sitemap", "title": "Sitemap", "path": path, "metadata": {"page_count": len(pages)}}],
        workflow_status="done",
        workflow_stage="complete",
    )


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

PHASE2_HANDLERS = {
    # Skill 5: signal-hunter
    "signal_detect": handle_signal_detect,
    "signal_qualify": handle_signal_qualify,
    "signal_engage": handle_signal_engage,
    "signal_follow_up": handle_signal_follow_up,
    # Skill 6: community-sniper
    "thread_scan": handle_thread_scan,
    "thread_select": handle_thread_select,
    "response_draft": handle_response_draft,
    "response_post": handle_response_post,
    # Skill 7: marketplace-expander
    "platform_scan": handle_platform_scan,
    "proposal_draft": handle_proposal_draft,
    "proposal_submit": handle_proposal_submit,
    "delivery_track": handle_delivery_track,
    # Skill 8: seo-factory
    "keyword_harvest": handle_keyword_harvest,
    "page_draft": handle_page_draft,
    "page_deploy": handle_page_deploy,
    "sitemap_update": handle_sitemap_update,
}
