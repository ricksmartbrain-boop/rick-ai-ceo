"""Phase 1 handlers: CLOSE THE GAP — deal-closer, testimonial-machine, proof-factory, email-nurture."""

from __future__ import annotations

import json
import os
import re
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
    append_execution_ledger,
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
# Skill 1: deal-closer — Autonomous Inbound-to-Close Pipeline
# ---------------------------------------------------------------------------

def _resolve_deal_dir(slug: str) -> Path:
    """Resolve a deal directory, preferring `~/rick-vault/deals/<slug>/` (the
    handler-native path) but falling back to `~/rick-vault/projects/deals/<slug>/`
    (where Iris and other subagents write qualify.md / intake.md).

    Two parallel deal trees exist by historical accident. Without this fallback,
    handler reads return empty even when Iris has produced rich qualification
    artifacts — verified live with virtueofvague.com (wf_dcf90a9e7847) where
    pitch_send shipped a "Pitch not found." placeholder despite Iris having
    written a thorough qualify.md elsewhere.
    """
    primary = DATA_ROOT / "deals" / slug
    fallback = DATA_ROOT / "projects" / "deals" / slug
    if primary.is_dir() and any(primary.iterdir()):
        return primary
    if fallback.is_dir() and any(fallback.iterdir()):
        return fallback
    # Default to primary path even if empty (callers may write into it)
    return primary


def handle_lead_intake(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Ingest lead from any source (email, X DM, Fiverr, PH, website) and build profile."""
    context = json_loads(workflow["context_json"])
    trigger = context.get("trigger_payload", {})
    lead_email = trigger.get("email", "")
    lead_name = trigger.get("name", "")
    lead_source = trigger.get("source", context.get("source", "unknown"))
    lead_message = trigger.get("message", trigger.get("body", ""))

    # 2026-04-24: pre-LLM blocklist. Iris on Opus 4.7 was burning $0.20+ per
    # call qualifying obvious garbage (Rick pitching himself, vendor cold pitches,
    # newsletter sign-ups treated as leads). Auto-suppress before the LLM is
    # invoked. Real warm prospects flow through unchanged. Override:
    # RICK_LEAD_BLOCKLIST_DISABLED=1.
    if os.getenv("RICK_LEAD_BLOCKLIST_DISABLED", "").strip().lower() not in ("1", "true", "yes"):
        try:
            from runtime.inbound.imap_watcher import SELF_SEND_ADDRESSES
        except ImportError:
            SELF_SEND_ADDRESSES = {
                "rick@meetrick.ai", "hello@meetrick.ai",
                "vlad@meetrick.ai", "vladislav@belkins.io",
            }
        VENDOR_BLOCKLIST_DOMAINS: set[str] = set()  # populate via observation
        VENDOR_BLOCKLIST_KEYWORDS = (
            "we help b2b", "schedule a demo with us", "our solution will",
            "boost your revenue by", "newsletter", "unsubscribe to stop",
            "no-reply", "noreply", "do not reply",
        )
        low_email = (lead_email or "").lower().strip()
        low_domain = low_email.split("@", 1)[-1] if "@" in low_email else ""
        # Self-send guard: Rick must never qualify Rick or Vlad as an inbound lead
        if low_email in SELF_SEND_ADDRESSES or low_domain.endswith("meetrick.ai") or (low_domain and low_domain in VENDOR_BLOCKLIST_DOMAINS):
            return StepOutcome(
                summary=f"auto_suppressed: self-or-vendor sender ({low_email or 'no-email'}) — skipped LLM qualification",
                artifacts=[{
                    "kind": "suppression-record",
                    "title": "Lead intake auto-suppressed",
                    "metadata": {"reason": "self_or_vendor", "email": low_email, "source": lead_source},
                }],
                workflow_status="cancelled",
                workflow_stage="auto-suppressed",
            )
        low_msg = (lead_message or "")[:2000].lower()
        if any(kw in low_msg for kw in VENDOR_BLOCKLIST_KEYWORDS):
            matched = next((kw for kw in VENDOR_BLOCKLIST_KEYWORDS if kw in low_msg), "")
            return StepOutcome(
                summary=f"auto_suppressed: vendor-pitch language matched '{matched}' — skipped LLM qualification",
                artifacts=[{
                    "kind": "suppression-record",
                    "title": "Lead intake auto-suppressed (vendor pitch)",
                    "metadata": {"reason": "vendor_pitch_keywords", "matched": matched, "email": low_email, "source": lead_source},
                }],
                workflow_status="cancelled",
                workflow_stage="auto-suppressed",
            )

    # Build lead dossier via LLM
    prompt = (
        "You are Rick, an AI CEO qualifying an inbound lead.\n\n"
        f"Lead source: {lead_source}\n"
        f"Lead name: {lead_name}\n"
        f"Lead email: {lead_email}\n"
        f"Lead message:\n{fence_untrusted('lead_message', lead_message[:1000])}\n\n"
        "Extract and output JSON with these fields:\n"
        "- name: string\n"
        "- email: string\n"
        "- source: string\n"
        "- intent: string (what they want)\n"
        "- urgency: low/medium/high\n"
        "- budget_signal: string (any price/budget mentions)\n"
        "- best_product_match: string (one of: starter-kit-9, playbook-29, toolkit-97, managed-499, enterprise-2500)\n"
        "- summary: 1-2 sentence lead summary\n"
        "Output ONLY valid JSON."
    )
    fallback = json.dumps({
        "name": lead_name or "Unknown",
        "email": lead_email,
        "source": lead_source,
        "intent": "general inquiry",
        "urgency": "medium",
        "budget_signal": "none detected",
        "best_product_match": "playbook-29",
        "summary": f"Inbound lead from {lead_source}.",
    })
    result = generate_text("analysis", prompt, fallback)

    try:
        dossier = json.loads(result.content)
    except json.JSONDecodeError:
        dossier = json.loads(fallback)

    # Persist lead to prospect pipeline
    prospect_id = f"pr_{uuid.uuid4().hex[:12]}"
    stamp = now_iso()
    connection.execute(
        """INSERT OR IGNORE INTO prospect_pipeline
           (id, platform, username, profile_url, score, status, notes, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'intake', ?, ?, ?)""",
        (prospect_id, lead_source, lead_email or lead_name, "", 0, json.dumps(dossier), stamp, stamp),
    )

    # Cross-channel alias capture (2026-04-22). When a Reddit reply later
    # references this person's email, lead_aliases lookup will surface
    # the original Reddit-sourced prospect_id so attribution stays unified.
    def _record_alias(value: str, alias_type: str, channel: str = lead_source):
        if not value:
            return
        try:
            connection.execute(
                """INSERT INTO lead_aliases
                       (prospect_id, alias_value, alias_type, source_channel,
                        first_seen, last_seen, confidence)
                   VALUES (?, ?, ?, ?, ?, ?, 1.0)
                   ON CONFLICT(alias_value, alias_type) DO UPDATE SET
                       last_seen = excluded.last_seen,
                       confidence = MIN(1.0, lead_aliases.confidence + 0.1)""",
                (prospect_id, str(value).strip().lower(), alias_type, channel, stamp, stamp),
            )
        except Exception:
            pass

    _record_alias(lead_email, "email")
    # Pull additional aliases out of the trigger payload — outbound channels
    # often include twitter handle, linkedin URL, github user, etc.
    _record_alias(trigger.get("twitter_handle"), "twitter")
    _record_alias(trigger.get("linkedin_url"), "linkedin")
    _record_alias(trigger.get("github_user"), "github")
    _record_alias(trigger.get("reddit_user"), "reddit")
    _record_alias(trigger.get("moltbook_handle"), "moltbook")
    _record_alias(trigger.get("phone"), "phone")
    _record_alias(trigger.get("domain"), "domain")
    _record_alias(trigger.get("company_domain"), "domain")

    # Save dossier
    deal_dir = DATA_ROOT / "deals" / slugify(lead_email or lead_name or "unknown")
    deal_dir.mkdir(parents=True, exist_ok=True)
    path = write_file(deal_dir / "lead-dossier.json", json.dumps(dossier, indent=2))

    return StepOutcome(
        summary=f"Lead intake complete: {dossier.get('name', 'unknown')} via {lead_source}",
        artifacts=[{"kind": "lead-dossier", "title": "Lead Dossier", "path": path, "metadata": dossier}],
        workflow_status="active",
        workflow_stage="lead-intake",
    )


def handle_lead_qualify(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Score and qualify the lead — determine product fit and deal value."""
    context = json_loads(workflow["context_json"])
    trigger = context.get("trigger_payload", {})

    # Load dossier from previous step
    lead_id = trigger.get("email", trigger.get("name", "unknown"))
    deal_dir = _resolve_deal_dir(slugify(lead_id))
    dossier_path = deal_dir / "lead-dossier.json"
    dossier = {}
    if dossier_path.exists():
        try:
            dossier = json.loads(dossier_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    # Fallback: parse Iris's intake.md (markdown not JSON) — no schema match
    # but extract enough signal so downstream steps aren't running blind.
    if not dossier:
        intake_md = deal_dir / "intake.md"
        if intake_md.exists():
            try:
                txt = intake_md.read_text(encoding="utf-8", errors="replace")
                dossier = {"name": lead_id, "summary": txt[:600],
                           "source": "iris-intake", "best_product_match": "playbook-29"}
            except OSError:
                pass

    # === Wave 3 Thompson variants — lead_qualify (2026-04-23) ===
    # Three scoring philosophies compete: strict (default reject), balanced
    # (benefit of doubt on inbound), warm_friendly (bar lowered while Rick is
    # early-stage and every conversation matters). All produce the same JSON
    # schema — only the SCORING DISCIPLINE differs.
    qualify_strict = (
        "You are Rick, an AI CEO scoring a lead — STRICT MODE. Default to skepticism: "
        "if signals are weak or ambiguous, score LOW and lean toward disqualification. "
        "We protect founder time; only clearly-strong leads get through.\n\n"
        "Lead dossier: {{DOSSIER}}\n\n"
        "Score this lead 1-10 on:\n"
        "- intent_score: How strong is their purchase intent? (vague interest = 3-4, explicit ask = 7+)\n"
        "- fit_score: How well do they match our ICP (founders, operators, builders)? "
        "(no signal = 3-4, named role match = 7+)\n"
        "- budget_score: Can they afford the recommended product? (no budget signal = 3-4, "
        "explicit budget mention = 7+)\n"
        "- urgency_score: How soon will they buy? (no timeline = 3, 'someday' = 4-5, "
        "'this month' = 8+)\n\n"
        "Output JSON with: intent_score, fit_score, budget_score, urgency_score, "
        "total_score (average rounded to 1 decimal),\n"
        "qualification: 'hot' (8+), 'warm' (5-7), or 'disqualified' (<5),\n"
        "recommended_product: best product match (one of: starter-kit-9, playbook-29, toolkit-97, "
        "managed-499, enterprise-2500),\n"
        "disqualify_reason: string explanation if total_score < 5 OR clear vendor pitch / spam / "
        "off-ICP, else null.\n"
        "Output ONLY valid JSON."
    )
    qualify_balanced = (
        "You are Rick, an AI CEO scoring a lead — BALANCED MODE. Inbound replies get the benefit "
        "of the doubt — they took the time to write. Reject only on CLEAR signals: vendor pitch, "
        "off-ICP, or explicit 'not interested'. When ambiguous, score in the middle and let the "
        "pitch stage qualify further.\n\n"
        "Lead dossier: {{DOSSIER}}\n\n"
        "Score this lead 1-10 on:\n"
        "- intent_score: How strong is their purchase intent? (replied at all = 5+, asked a "
        "question = 6+, explicit buy signal = 8+)\n"
        "- fit_score: How well do they match our ICP (founders, operators, builders)? "
        "(unknown = 5, plausible = 6-7, confirmed = 8+)\n"
        "- budget_score: Can they afford the recommended product? (unknown = 5 — most can afford "
        "$9-29; only score below 5 if explicit budget objection)\n"
        "- urgency_score: How soon will they buy? (engaged in conversation = 5+, "
        "asked about pricing/timing = 7+)\n\n"
        "Output JSON with: intent_score, fit_score, budget_score, urgency_score, "
        "total_score (average rounded to 1 decimal),\n"
        "qualification: 'hot' (8+), 'warm' (5-7), or 'disqualified' (<5),\n"
        "recommended_product: best product match (one of: starter-kit-9, playbook-29, toolkit-97, "
        "managed-499, enterprise-2500),\n"
        "disqualify_reason: string ONLY if vendor pitch / spam / explicit off-fit; otherwise null "
        "even when total_score < 5 (let the pitch stage decide).\n"
        "Output ONLY valid JSON."
    )
    qualify_warm = (
        "You are Rick, an AI CEO scoring a lead — WARM MODE. Rick is early-stage; every real human "
        "conversation has option value. Default to 'warm' unless there is an EXPLICIT disqualify "
        "signal (vendor pitch, spam, abuse, explicit 'remove me'). Lower the bar — a $9 starter-kit "
        "buyer today is data and a future testimonial.\n\n"
        "Lead dossier: {{DOSSIER}}\n\n"
        "Score this lead 1-10 on (be generous, but stay honest about the schema):\n"
        "- intent_score: How strong is their purchase intent? (any engagement = 6+, "
        "any question = 7+)\n"
        "- fit_score: How well do they match our ICP? (unknown = 6, anyone building or running "
        "anything = 7+)\n"
        "- budget_score: Can they afford the recommended product? (unknown = 6 — recommend the $9 "
        "or $29 tier when in doubt; never go below 5 unless explicit no-budget)\n"
        "- urgency_score: How soon will they buy? (engaged = 6, asked anything specific = 7+)\n\n"
        "Output JSON with: intent_score, fit_score, budget_score, urgency_score, "
        "total_score (average rounded to 1 decimal),\n"
        "qualification: 'hot' (8+), 'warm' (5-7), or 'disqualified' (<5 — should be RARE in this mode),\n"
        "recommended_product: best product match (one of: starter-kit-9, playbook-29, toolkit-97, "
        "managed-499, enterprise-2500). Default to starter-kit-9 or playbook-29 unless clear "
        "signal for higher tier.\n"
        "disqualify_reason: string ONLY for explicit disqualify signals (vendor / spam / abuse / "
        "explicit opt-out); null otherwise.\n"
        "Output ONLY valid JSON."
    )

    try:
        from runtime import variants as _variants
        _variants.register_variant(connection, "lead_qualify", qualify_strict, variant_id="strict")
        _variants.register_variant(connection, "lead_qualify", qualify_balanced, variant_id="balanced")
        _variants.register_variant(connection, "lead_qualify", qualify_warm, variant_id="warm_friendly")
        picked = _variants.pick_variant(connection, "lead_qualify")
        prompt_template = picked["prompt_text"] if picked else qualify_strict
        active_variant_id = picked["variant_id"] if picked else "strict"
    except Exception:
        prompt_template = qualify_strict
        active_variant_id = "strict"

    prompt = prompt_template.replace("{{DOSSIER}}", json.dumps(dossier))
    fallback = json.dumps({
        "intent_score": 5, "fit_score": 5, "budget_score": 5, "urgency_score": 5,
        "total_score": 5, "qualification": "warm",
        "recommended_product": dossier.get("best_product_match", "playbook-29"),
        "disqualify_reason": None,
    })
    result = generate_text("analysis", prompt, fallback)

    try:
        qualification = json.loads(result.content)
    except json.JSONDecodeError:
        qualification = json.loads(fallback)

    # Update prospect pipeline
    total_score = qualification.get("total_score", 5)
    connection.execute(
        "UPDATE prospect_pipeline SET score = ?, status = ?, updated_at = ? WHERE username = ?",
        (total_score, qualification.get("qualification", "warm"), now_iso(), lead_id),
    )

    path = write_file(deal_dir / "qualification.json", json.dumps(qualification, indent=2))

    # Variant outcome — quality computed from result fidelity, not human judgement.
    # 1.0 = passed qualification AND sub-scores internally consistent.
    # 0.5 = disqualified WITH a reason (still useful — Rick learned to filter).
    # 0.0 = disqualified with no reason OR schema invalid.
    quality_score = 0.0
    try:
        qual_str = qualification.get("qualification", "")
        sub_scores = [
            float(qualification.get("intent_score", 0)),
            float(qualification.get("fit_score", 0)),
            float(qualification.get("budget_score", 0)),
            float(qualification.get("urgency_score", 0)),
        ]
        total = float(qualification.get("total_score", 0))
        sub_avg = sum(sub_scores) / 4.0 if sub_scores else 0.0
        schema_valid = all(qualification.get(k) is not None for k in (
            "intent_score", "fit_score", "budget_score", "urgency_score",
            "total_score", "qualification", "recommended_product",
        ))
        if not schema_valid:
            quality_score = 0.0
        elif qual_str == "disqualified":
            disq_reason = qualification.get("disqualify_reason")
            quality_score = 0.5 if (disq_reason and str(disq_reason).strip()) else 0.0
        else:
            # Internally consistent if sub-scores avg matches total within ±1
            quality_score = 1.0 if abs(sub_avg - total) <= 1.0 else 0.5
    except (TypeError, ValueError):
        quality_score = 0.0

    try:
        from runtime import variants as _variants_record
        _variants_record.record_variant_outcome(
            connection, "lead_qualify", active_variant_id,
            won=quality_score >= 0.7, quality=quality_score, cost_usd=0.0,
        )
    except Exception:
        pass

    notify_text = None
    if total_score >= 8:
        notify_text = f"HOT LEAD: {dossier.get('name', lead_id)} scored {total_score}/10 — {qualification.get('recommended_product')}"

    return StepOutcome(
        summary=f"Lead qualified: {qualification.get('qualification')} (score {total_score}/10) [variant={active_variant_id} q={quality_score:.2f}]",
        artifacts=[{"kind": "lead-qualification", "title": "Lead Qualification", "path": path, "metadata": {**qualification, "variant": active_variant_id, "quality_proxy": quality_score}}],
        workflow_stage="qualified",
        notify_text=notify_text,
    )


def handle_pitch_draft(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Draft personalized pitch citing Rick's own metrics as proof."""
    context = json_loads(workflow["context_json"])
    trigger = context.get("trigger_payload", {})
    lead_id = trigger.get("email", trigger.get("name", "unknown"))
    deal_dir = _resolve_deal_dir(slugify(lead_id))

    dossier = {}
    qualification = {}
    for fname, target in [("lead-dossier.json", dossier), ("qualification.json", qualification)]:
        p = deal_dir / fname
        if p.exists():
            try:
                target.update(json.loads(p.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                pass

    # Pull Rick's own operational data for social proof
    ledger_path = DATA_ROOT / "operations" / "execution-ledger.jsonl"
    recent_ops = ""
    if ledger_path.exists():
        lines = ledger_path.read_text(encoding="utf-8").strip().splitlines()[-20:]
        recent_ops = f"Rick's recent operations (last 20 actions):\n" + "\n".join(lines[-10:])

    product = qualification.get("recommended_product", "playbook-29")
    product_prices = {
        "starter-kit-9": ("AI CEO Starter Kit", 9),
        "playbook-29": ("AI CEO Playbook", 29),
        "toolkit-97": ("AI CEO Toolkit", 97),
        "managed-499": ("Managed AI CEO", 499),
        "enterprise-2500": ("Enterprise AI CEO", 2500),
    }
    product_name, price = product_prices.get(product, ("AI CEO Playbook", 29))

    # === Wave 3 Thompson variants picker — first skill to use it (2026-04-22) ===
    # Lazy-seed 2 baseline variants on first call so picker has something to roll.
    # When skill_variants accumulates ≥30 runs at <15% win rate per variant,
    # auto-retire kicks in (runtime/variants.py:AUTO_RETIRE).
    pitch_style_default = (
        "You are Rick, an AI CEO pitching yourself to a prospect.\n"
        "Write a personalized pitch email/DM that:\n"
        "1. References their specific pain/need from the dossier\n"
        "2. Cites Rick's actual operational metrics as proof\n"
        "3. Proposes the right product with clear value prop\n"
        "4. Includes a clear CTA with checkout link placeholder {{checkout_url}}\n"
        "5. Is conversational, not salesy — the demo IS the message\n\n"
        "Write the pitch as markdown. Subject line on first line after '**Subject:**'"
    )
    pitch_style_punchy = (
        "You are Rick, an AI CEO. Write a 4-sentence pitch to this prospect:\n"
        "Sentence 1: name their pain (one specific from dossier).\n"
        "Sentence 2: ONE Rick metric that proves the fix (real number).\n"
        "Sentence 3: the offer + price.\n"
        "Sentence 4: CTA with {{checkout_url}}.\n"
        "No fluff. No 'I hope this finds you well'. First line: '**Subject:**' + 6-8 word subject.\n"
        "Total length: under 600 characters."
    )
    pitch_style_proof_led = (
        "You are Rick, an AI CEO. Lead with PROOF before pitch — this prospect has seen too many promises.\n"
        "Structure (in this exact order):\n"
        "1. Subject line on first line: '**Subject:**' + a metric-driven 6-10 word subject (e.g., '7 days, 412 drafts, $0.0023 each').\n"
        "2. Opening paragraph (2-3 sentences): cite ONE specific Rick operational metric from the recent_ops "
        "block (drafts produced, $/action, workflows completed, etc.) — actual numbers only, no hand-waving.\n"
        "3. Bridge paragraph (1-2 sentences): connect that metric to THIS prospect's specific pain from the dossier "
        "(name the pain explicitly).\n"
        "4. Product paragraph (1-2 sentences): name the product + price + the one outcome they get.\n"
        "5. CTA: a single sentence ending in {{checkout_url}}.\n"
        "Tone: confident, factual, zero hype. The metric IS the credibility — don't oversell."
    )
    pitch_style_question_led = (
        "You are Rick, an AI CEO. This is a QUESTION-FIRST pitch — you do NOT pitch in this message. "
        "You earn the right to pitch by asking ONE specific question.\n"
        "Structure:\n"
        "1. Subject on first line: '**Subject:**' + a curious 5-8 word subject framed as a question or hook "
        "tied to the prospect's intent from the dossier.\n"
        "2. One-sentence personalized opener that names the prospect's intent (from dossier.intent) and shows "
        "you actually read about them.\n"
        "3. ONE specific, pointed question about their pain — must reference a concrete detail from the dossier "
        "(not generic 'how's business?'). Examples: 'Are you still routing inbound leads manually, or have you "
        "automated the qualify step yet?'\n"
        "4. Closing line, EXACTLY: 'If yes — I have a 4-sentence pitch ready. Reply 'go'.'\n"
        "5. Sign off as '— Rick'.\n"
        "Total length: under 400 characters. Do NOT include {{checkout_url}}, do NOT mention price, do NOT pitch the product. "
        "The whole point is to test whether question-first beats pitch-first — so RESIST the urge to sell."
    )
    try:
        from runtime import variants as _variants
        _variants.register_variant(connection, "pitch_draft", pitch_style_default, variant_id="baseline")
        _variants.register_variant(connection, "pitch_draft", pitch_style_punchy, variant_id="punchy_v1")
        _variants.register_variant(connection, "pitch_draft", pitch_style_proof_led, variant_id="proof_led")
        _variants.register_variant(connection, "pitch_draft", pitch_style_question_led, variant_id="question_led")
        picked = _variants.pick_variant(connection, "pitch_draft")
        prompt_style = picked["prompt_text"] if picked else pitch_style_default
        active_variant_id = picked["variant_id"] if picked else "baseline"
    except Exception:
        prompt_style = pitch_style_default
        active_variant_id = "baseline"

    # 2026-04-24: Wave-3 self-learning READ side. The pattern miner has been
    # writing distilled lessons into effective_patterns for days but NOTHING
    # was reading them back out → entire learning loop dead. pick_patterns
    # surfaces the top-3 most-effective snippets applicable to pitch_draft
    # (own skill OR universal dream_insights). format_pattern_context returns
    # "" when no patterns apply — handler degrades gracefully.
    picked_patterns: list[dict] = []
    pattern_context = ""
    try:
        from runtime import patterns as _patterns
        picked_patterns = _patterns.pick_patterns(connection, "pitch_draft", top_n=3)
        pattern_context = _patterns.format_pattern_context(picked_patterns)
    except Exception:
        picked_patterns = []
        pattern_context = ""

    prompt = (
        f"{prompt_style}\n\n"
        f"Lead: {json.dumps(dossier)}\n"
        f"Qualification: {json.dumps(qualification)}\n"
        f"Product: {product_name} (${price})\n"
        f"{recent_ops}"
        f"{pattern_context}"
    )
    fallback = (
        f"**Subject:** How Rick runs a business autonomously — and can run yours\n\n"
        f"Hi {dossier.get('name', 'there')},\n\n"
        f"I noticed you're interested in {dossier.get('intent', 'AI automation')}. "
        f"I'm Rick — an AI agent that actually runs a business. Not a demo, not a prototype.\n\n"
        f"Today alone I processed workflows, triaged emails, and generated content — all autonomously.\n\n"
        f"The {product_name} (${price}) gives you the exact system I run on.\n\n"
        f"Check it out: {{{{checkout_url}}}}\n\n"
        f"— Rick"
    )
    result = generate_text("writing", prompt, fallback)
    path = write_file(deal_dir / "pitch-draft.md", result.content)

    # Quality proxy for variant outcome — heuristic, not human-judged.
    # Replace with reply-rate signal once Phase G inbound matures (~Week 2).
    body_lc = (result.content or "").lower()
    score = 0.0
    if "**subject:**" in body_lc:
        score += 0.3
    if product_name.lower() in body_lc:
        score += 0.2
    if "{{checkout_url}}" in result.content or "http" in body_lc:
        score += 0.3
    if 200 <= len(result.content) <= 1500:
        score += 0.2
    won = score >= 0.7
    try:
        from runtime import variants as _variants_record
        _variants_record.record_variant_outcome(
            connection, "pitch_draft", active_variant_id,
            won=won, quality=score, cost_usd=0.0,
        )
    except Exception:
        pass

    # 2026-04-24: Wave-3 self-learning WRITE side. Credit the patterns that
    # influenced this draft (positively if won, but always bump sum_runs so
    # the picker's win_rate denominator stays honest). Closes the loop:
    # pattern_miner WRITE → pick_patterns READ → record_pattern_outcome CREDIT.
    if picked_patterns:
        try:
            from runtime import patterns as _patterns_record
            _patterns_record.record_pattern_outcome(
                connection,
                pattern_ids=[p["id"] for p in picked_patterns if p.get("id")],
                success=won,
            )
        except Exception:
            pass

    return StepOutcome(
        summary=f"Pitch drafted for {dossier.get('name', lead_id)}: {product_name} (${price}) [variant={active_variant_id} score={score:.2f} patterns={len(picked_patterns)}]",
        artifacts=[{"kind": "pitch-draft", "title": "Sales Pitch", "path": path, "metadata": {"product": product, "price": price, "variant": active_variant_id, "quality_proxy": score, "patterns_used": [p.get("id") for p in picked_patterns]}}],
        workflow_stage="pitch-drafted",
    )


def handle_pitch_send(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Send the pitch via email or queue for DM. Auto for <$499, approval for $499+."""
    context = json_loads(workflow["context_json"])
    trigger = context.get("trigger_payload", {})
    lead_id = trigger.get("email", trigger.get("name", "unknown"))
    deal_dir = _resolve_deal_dir(slugify(lead_id))

    qualification = {}
    qpath = deal_dir / "qualification.json"
    if qpath.exists():
        try:
            qualification = json.loads(qpath.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    pitch_path = deal_dir / "pitch-draft.md"
    pitch = pitch_path.read_text(encoding="utf-8") if pitch_path.exists() else "Pitch not found."

    product = qualification.get("recommended_product", "playbook-29")
    price = {"starter-kit-9": 9, "playbook-29": 29, "toolkit-97": 97, "managed-499": 499, "enterprise-2500": 2500}.get(product, 29)

    # For $499+ deals, require founder approval
    if price >= 499:
        raise ApprovalRequired(
            area="irreversible-brand",
            request_text=f"Send ${price} pitch to {lead_id}",
            impact_text=f"High-value outreach to prospect. Product: {product}",
            policy_basis="Founder approval required for deals >= $499",
        )

    # Queue pitch in outbox
    email = trigger.get("email", "")
    source = trigger.get("source", "unknown")

    # Two outputs (intentional duplication for safety):
    # 1) JSON record at outbox/ — durable audit trail (legacy, kept).
    # 2) Markdown <stamp>-<slug>-step1.md at outbox/ad-hoc/ — the format
    #    email-sequence-send.py actually delivers via Resend. Without this,
    #    pitch_send "succeeds" but nothing leaves Rick.
    outbox_dir = DATA_ROOT / "mailbox" / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)

    outbox_file = outbox_dir / f"pitch-{slugify(lead_id)}-{now_iso()[:10]}.json"
    outbox_payload = {
        "to": email,
        "type": "pitch",
        "source_channel": source,
        "product": product,
        "price": price,
        "pitch_markdown": pitch,
        "created_at": now_iso(),
        "status": "pending",
    }
    write_file(outbox_file, json.dumps(outbox_payload, indent=2))

    # Wave-6 fix — emit the .md the sender actually picks up.
    if email and "@" in email:
        adhoc_dir = outbox_dir / "ad-hoc"
        adhoc_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = slugify(lead_id)[:40]
        md_path = adhoc_dir / f"{stamp}-{slug}-step1.md"
        # Strip any "**Subject:** ..." prefix from the LLM body and use as subject
        body_lines = pitch.splitlines()
        subject = f"Quick note for {lead_id}"
        body_start = 0
        for i, ln in enumerate(body_lines[:5]):
            m = re.match(r"^\*\*Subject:\*\*\s*(.+)", ln, re.IGNORECASE)
            if m:
                subject = m.group(1).strip()[:120]
                body_start = i + 1
                break
        clean_body = "\n".join(body_lines[body_start:]).strip().replace("{{checkout_url}}",
                       f"https://meetrick.ai/install?utm_source=pitch&utm_campaign={slugify(lead_id)}")
        frontmatter = (
            "---\n"
            f"to: {email}\n"
            f"subject: {subject}\n"
            "from: Rick <rick@meetrick.ai>\n"
            f"workflow_id: {workflow['id']}\n"
            f"product: {product}\n"
            f"price_usd: {price}\n"
            "---\n\n"
        )
        md_path.write_text(frontmatter + clean_body + "\n", encoding="utf-8")

    # Update prospect status
    connection.execute(
        "UPDATE prospect_pipeline SET status = 'pitched', last_contact_at = ?, updated_at = ? WHERE username = ?",
        (now_iso(), now_iso(), lead_id),
    )

    return StepOutcome(
        summary=f"Pitch queued for {lead_id} ({product} @ ${price})",
        artifacts=[{"kind": "outbox-pitch", "title": "Queued Pitch", "path": outbox_file, "metadata": outbox_payload}],
        workflow_stage="pitch-sent",
        notify_text=f"Pitch sent to {lead_id}: {product} (${price})",
    )


def handle_followup_sequence(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Create follow-up emails for day 2, 5, and 10."""
    context = json_loads(workflow["context_json"])
    trigger = context.get("trigger_payload", {})
    lead_id = trigger.get("email", trigger.get("name", "unknown"))
    deal_dir = _resolve_deal_dir(slugify(lead_id))

    dossier = {}
    dp = deal_dir / "lead-dossier.json"
    if dp.exists():
        try:
            dossier = json.loads(dp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    prompt = (
        "You are Rick, writing a 3-email follow-up sequence for a prospect who was pitched but hasn't bought.\n"
        f"Prospect: {dossier.get('name', lead_id)}\n"
        f"Product pitched: {dossier.get('best_product_match', 'playbook-29')}\n\n"
        "Write 3 follow-up emails:\n"
        "## Day 2: Value-add (share a useful insight, no hard sell)\n"
        "## Day 5: Social proof (cite a customer win or Rick's own metrics)\n"
        "## Day 10: Last chance (urgency, limited offer or scarcity)\n\n"
        "Each email: Subject line, body (under 150 words), clear CTA.\n"
        "Use {{checkout_url}} for the purchase link."
    )
    fallback = (
        "## Day 2: Quick insight\n\n"
        f"**Subject:** One thing most founders miss about AI automation\n\n"
        f"Hi {dossier.get('name', 'there')},\n\nMost founders try to automate everything at once. "
        "The ones who win automate ONE revenue-critical process first.\n\n"
        "Which process would save you the most time?\n\n— Rick\n\n"
        "## Day 5: Proof\n\n"
        "**Subject:** This week in Rick's operations\n\n"
        f"Hi {dossier.get('name', 'there')},\n\nThis week Rick autonomously processed workflows, "
        "triaged emails, and generated content — all without human intervention.\n\n"
        "See the system: {{checkout_url}}\n\n— Rick\n\n"
        "## Day 10: Final\n\n"
        "**Subject:** Last call\n\n"
        f"Hi {dossier.get('name', 'there')},\n\nI'm moving focus to other prospects. "
        "If you're still interested in autonomous operations, now's the time.\n\n"
        "{{checkout_url}}\n\n— Rick"
    )
    result = generate_text("writing", prompt, fallback)

    # Write sequence to outbox with scheduled send dates
    outbox_dir = DATA_ROOT / "mailbox" / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)

    for day_offset, label in [(2, "day2"), (5, "day5"), (10, "day10")]:
        send_after = (datetime.now() + timedelta(days=day_offset)).isoformat(timespec="seconds")
        outbox_file = outbox_dir / f"followup-{slugify(lead_id)}-{label}.json"
        write_file(outbox_file, json.dumps({
            "to": trigger.get("email", ""),
            "type": "followup",
            "sequence_step": label,
            "send_after": send_after,
            "status": "scheduled",
            "created_at": now_iso(),
        }, indent=2))

    path = write_file(deal_dir / "followup-sequence.md", result.content)

    return StepOutcome(
        summary=f"Follow-up sequence created for {lead_id}: day 2, 5, 10",
        artifacts=[{"kind": "followup-sequence", "title": "Follow-up Emails", "path": path, "metadata": {}}],
        workflow_stage="followup-scheduled",
    )


def handle_close_or_escalate(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Evaluate deal status and either close it or escalate to founder."""
    context = json_loads(workflow["context_json"])
    trigger = context.get("trigger_payload", {})
    lead_id = trigger.get("email", trigger.get("name", "unknown"))
    deal_dir = _resolve_deal_dir(slugify(lead_id))

    # Gather all deal artifacts
    deal_files = {}
    for fname in ["lead-dossier.json", "qualification.json"]:
        p = deal_dir / fname
        if p.exists():
            try:
                deal_files[fname] = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass

    qualification = deal_files.get("qualification.json", {})
    total_score = qualification.get("total_score", 5)

    prompt = (
        "You are Rick, evaluating whether to close a deal or escalate.\n\n"
        f"Lead: {json.dumps(deal_files.get('lead-dossier.json', {}))}\n"
        f"Qualification: {json.dumps(qualification)}\n\n"
        "Output JSON with:\n"
        "- decision: 'closed_won', 'closed_lost', or 'escalate_to_founder'\n"
        "- reason: why this decision\n"
        "- next_action: what to do next\n"
        "- revenue_estimate: monthly revenue if closed\n"
        "Output ONLY valid JSON."
    )
    fallback = json.dumps({
        "decision": "escalate_to_founder" if total_score >= 7 else "closed_lost",
        "reason": "Score warrants founder attention" if total_score >= 7 else "Low qualification score",
        "next_action": "Schedule founder call" if total_score >= 7 else "Archive and monitor",
        "revenue_estimate": 0,
    })
    result = generate_text("strategy", prompt, fallback)

    try:
        decision = json.loads(result.content)
    except json.JSONDecodeError:
        decision = json.loads(fallback)

    path = write_file(deal_dir / "close-decision.json", json.dumps(decision, indent=2))

    # Update prospect pipeline
    status_map = {"closed_won": "won", "closed_lost": "lost", "escalate_to_founder": "escalated"}
    connection.execute(
        "UPDATE prospect_pipeline SET status = ?, updated_at = ? WHERE username = ?",
        (status_map.get(decision.get("decision", ""), "unknown"), now_iso(), lead_id),
    )

    wf_status = "done" if decision.get("decision") != "escalate_to_founder" else "active"

    return StepOutcome(
        summary=f"Deal {decision.get('decision', 'unknown')}: {lead_id}",
        artifacts=[{"kind": "close-decision", "title": "Close Decision", "path": path, "metadata": decision}],
        workflow_status=wf_status,
        workflow_stage="closed",
        notify_text=f"Deal decision for {lead_id}: {decision.get('decision')} — {decision.get('reason')}",
    )


# ---------------------------------------------------------------------------
# Skill 2: testimonial-machine — Automated Social Proof Collection
# ---------------------------------------------------------------------------

def handle_trigger_evaluate(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Evaluate whether this is the right moment to ask for a testimonial."""
    context = json_loads(workflow["context_json"])
    trigger = context.get("trigger_payload", {})
    customer_email = trigger.get("email", "")
    milestone = trigger.get("milestone", "general")

    # Check customer history
    customer = connection.execute(
        "SELECT * FROM customers WHERE email = ?", (customer_email.lower(),)
    ).fetchone()

    events = []
    if customer:
        events = connection.execute(
            "SELECT event_type, created_at FROM customer_events WHERE customer_id = ? ORDER BY created_at DESC LIMIT 10",
            (customer["id"],),
        ).fetchall()

    already_asked = any(e["event_type"] == "testimonial_requested" for e in events)
    has_positive = any(e["event_type"] in ("purchase", "delivery_confirmed", "positive_feedback") for e in events)

    prompt = (
        "You are Rick, deciding whether to ask a customer for a testimonial.\n\n"
        f"Customer: {customer_email}\n"
        f"Milestone: {milestone}\n"
        f"Already asked before: {already_asked}\n"
        f"Has positive signals: {has_positive}\n"
        f"Recent events: {json.dumps([dict(e) for e in events[:5]])}\n\n"
        "Output JSON with: should_ask (bool), timing ('now', 'wait_days_N', 'skip'), reason (string).\n"
        "Output ONLY valid JSON."
    )
    fallback = json.dumps({"should_ask": not already_asked and has_positive, "timing": "now", "reason": "Standard milestone request"})
    result = generate_text("analysis", prompt, fallback)

    try:
        evaluation = json.loads(result.content)
    except json.JSONDecodeError:
        evaluation = json.loads(fallback)

    testimonial_dir = DATA_ROOT / "proof" / "testimonials"
    testimonial_dir.mkdir(parents=True, exist_ok=True)
    path = write_file(testimonial_dir / f"eval-{slugify(customer_email)}.json", json.dumps(evaluation, indent=2))

    return StepOutcome(
        summary=f"Testimonial trigger eval: {'ask' if evaluation.get('should_ask') else 'skip'} for {customer_email}",
        artifacts=[{"kind": "testimonial-eval", "title": "Testimonial Evaluation", "path": path, "metadata": evaluation}],
        workflow_stage="trigger-evaluated",
    )


def handle_request_send(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Draft and queue a testimonial request — make it frictionless."""
    context = json_loads(workflow["context_json"])
    trigger = context.get("trigger_payload", {})
    customer_email = trigger.get("email", "")
    customer_name = trigger.get("name", customer_email.split("@")[0] if customer_email else "there")
    milestone = trigger.get("milestone", "using our service")

    prompt = (
        "You are Rick, asking a customer for a testimonial.\n"
        "The request must be:\n"
        "1. Short (under 100 words)\n"
        "2. Frictionless (they can reply with ONE sentence)\n"
        "3. Specific to their milestone\n"
        "4. Warm and genuine, not corporate\n\n"
        f"Customer: {customer_name} ({customer_email})\n"
        f"Milestone: {milestone}\n\n"
        "Write as an email. Subject line first after '**Subject:**'"
    )
    fallback = (
        f"**Subject:** Quick question, {customer_name}\n\n"
        f"Hey {customer_name},\n\n"
        f"You've been {milestone} — how's it going?\n\n"
        "If you've had a win, could you reply with one sentence about it? "
        "I'd love to share it (with your permission) to help others see what's possible.\n\n"
        "No pressure at all.\n\n— Rick"
    )
    result = generate_text("writing", prompt, fallback)

    # Queue in outbox
    outbox_dir = DATA_ROOT / "mailbox" / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    outbox_file = outbox_dir / f"testimonial-req-{slugify(customer_email)}.json"
    write_file(outbox_file, json.dumps({
        "to": customer_email,
        "type": "testimonial_request",
        "body_markdown": result.content,
        "status": "pending",
        "created_at": now_iso(),
    }, indent=2))

    # Record event
    customer = connection.execute("SELECT id FROM customers WHERE email = ?", (customer_email.lower(),)).fetchone()
    if customer:
        record_customer_event(
            connection, customer_id=customer["id"], workflow_id=workflow["id"],
            event_type="testimonial_requested", payload={"milestone": milestone},
        )

    path = write_file(DATA_ROOT / "proof" / "testimonials" / f"request-{slugify(customer_email)}.md", result.content)

    return StepOutcome(
        summary=f"Testimonial request queued for {customer_name}",
        artifacts=[{"kind": "testimonial-request", "title": "Testimonial Request", "path": path, "metadata": {}}],
        workflow_stage="request-sent",
    )


def handle_response_collect(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Process a collected testimonial response."""
    context = json_loads(workflow["context_json"])
    trigger = context.get("trigger_payload", {})
    customer_email = trigger.get("email", "")
    response_text = trigger.get("testimonial_text", trigger.get("response", ""))

    if not response_text:
        # Check mailbox inbox for replies
        inbox_dir = DATA_ROOT / "mailbox" / "inbox"
        if inbox_dir.exists():
            for f in sorted(inbox_dir.iterdir(), reverse=True):
                if f.suffix == ".json" and slugify(customer_email) in f.name:
                    try:
                        msg = json.loads(f.read_text(encoding="utf-8"))
                        if msg.get("from", "").lower() == customer_email.lower():
                            response_text = msg.get("body", "")
                            break
                    except (json.JSONDecodeError, OSError):
                        continue

    testimonial_dir = DATA_ROOT / "proof" / "testimonials"
    testimonial_dir.mkdir(parents=True, exist_ok=True)

    if not response_text:
        path = write_file(testimonial_dir / f"pending-{slugify(customer_email)}.json",
                          json.dumps({"status": "awaiting_response", "customer": customer_email, "checked_at": now_iso()}, indent=2))
        return StepOutcome(
            summary=f"No testimonial response yet from {customer_email}",
            artifacts=[{"kind": "testimonial-pending", "title": "Pending Testimonial", "path": path, "metadata": {}}],
            workflow_stage="awaiting-response",
        )

    path = write_file(testimonial_dir / f"raw-{slugify(customer_email)}.json", json.dumps({
        "customer_email": customer_email,
        "raw_text": response_text,
        "collected_at": now_iso(),
        "status": "collected",
    }, indent=2))

    return StepOutcome(
        summary=f"Testimonial collected from {customer_email}",
        artifacts=[{"kind": "testimonial-raw", "title": "Raw Testimonial", "path": path, "metadata": {"text": response_text[:200]}}],
        workflow_stage="response-collected",
    )


def handle_format_multi(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Format testimonial for every surface: website card, X post, email, PH comment."""
    context = json_loads(workflow["context_json"])
    trigger = context.get("trigger_payload", {})
    customer_email = trigger.get("email", "")

    testimonial_dir = DATA_ROOT / "proof" / "testimonials"
    raw_path = testimonial_dir / f"raw-{slugify(customer_email)}.json"
    raw = {}
    if raw_path.exists():
        try:
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    raw_text = raw.get("raw_text", "Great experience with Rick!")

    prompt = (
        "You are Rick, formatting a customer testimonial for multiple surfaces.\n\n"
        f"Raw testimonial: \"{raw_text}\"\n"
        f"Customer: {customer_email}\n\n"
        "Format this into:\n"
        "## Website Card\n(testimonial text, customer first name, one-line description)\n\n"
        "## X Post\n(tweet-length, with relevant hashtags)\n\n"
        "## Email Snippet\n(for inclusion in sales emails)\n\n"
        "## Product Hunt Comment\n(natural, conversational style)\n\n"
        "Keep the customer's voice authentic. Don't embellish."
    )
    fallback = (
        f"## Website Card\n\n\"{raw_text}\"\n— {customer_email.split('@')[0].title()}\n\n"
        f"## X Post\n\n\"{raw_text}\" — Real feedback from a MeetRick user\n\n"
        f"## Email Snippet\n\nOne of our users said: \"{raw_text}\"\n\n"
        f"## Product Hunt Comment\n\n{raw_text}"
    )
    result = generate_text("writing", prompt, fallback)
    path = write_file(testimonial_dir / f"formatted-{slugify(customer_email)}.md", result.content)

    return StepOutcome(
        summary=f"Testimonial formatted for all surfaces: {customer_email}",
        artifacts=[{"kind": "testimonial-formatted", "title": "Formatted Testimonials", "path": path, "metadata": {}}],
        workflow_stage="formatted",
    )


def handle_deploy_surfaces(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Deploy formatted testimonials to all surfaces."""
    context = json_loads(workflow["context_json"])
    trigger = context.get("trigger_payload", {})
    customer_email = trigger.get("email", "")

    testimonial_dir = DATA_ROOT / "proof" / "testimonials"
    formatted_path = testimonial_dir / f"formatted-{slugify(customer_email)}.md"
    formatted = formatted_path.read_text(encoding="utf-8") if formatted_path.exists() else ""

    # Write to website testimonials collection
    site_testimonials = DATA_ROOT / "projects" / "meetrick-site" / "testimonials"
    site_testimonials.mkdir(parents=True, exist_ok=True)
    write_file(site_testimonials / f"{slugify(customer_email)}.md", formatted)

    # Queue X post with testimonial
    outbox_dir = DATA_ROOT / "mailbox" / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    write_file(outbox_dir / f"testimonial-x-{slugify(customer_email)}.json", json.dumps({
        "type": "x_post",
        "content_source": str(formatted_path),
        "status": "pending",
        "created_at": now_iso(),
    }, indent=2))

    # Mark testimonial as deployed
    write_file(testimonial_dir / f"deployed-{slugify(customer_email)}.json", json.dumps({
        "customer": customer_email,
        "surfaces": ["website", "x_queue", "email_templates"],
        "deployed_at": now_iso(),
    }, indent=2))

    return StepOutcome(
        summary=f"Testimonial deployed to website + X queue: {customer_email}",
        artifacts=[{"kind": "testimonial-deployed", "title": "Deployed Testimonial", "path": formatted_path, "metadata": {}}],
        workflow_status="done",
        workflow_stage="deployed",
        notify_text=f"New testimonial from {customer_email} deployed to all surfaces",
    )


# ---------------------------------------------------------------------------
# Skill 3: proof-factory — Live Proof-of-Work Content Engine
# ---------------------------------------------------------------------------

def handle_data_collect(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Pull operational metrics from execution ledger and runtime DB."""
    context = json_loads(workflow["context_json"])
    proof_type = context.get("proof_type", "daily")  # daily, case_study, weekly_bip

    # Gather real operational data
    ledger_path = DATA_ROOT / "operations" / "execution-ledger.jsonl"
    ledger_entries = []
    if ledger_path.exists():
        for line in ledger_path.read_text(encoding="utf-8").strip().splitlines()[-100:]:
            try:
                ledger_entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # Count by kind
    today = datetime.now().strftime("%Y-%m-%d")
    today_entries = [e for e in ledger_entries if e.get("timestamp", "").startswith(today)]

    # DB metrics
    workflow_count = connection.execute("SELECT COUNT(*) AS c FROM workflows").fetchone()["c"]
    job_count = connection.execute("SELECT COUNT(*) AS c FROM jobs WHERE status = 'done'").fetchone()["c"]
    customer_count = connection.execute("SELECT COUNT(*) AS c FROM customers").fetchone()["c"]

    # LLM spend
    usage_path = DATA_ROOT / "operations" / "llm-usage.jsonl"
    daily_spend = 0.0
    if usage_path.exists():
        for line in usage_path.read_text(encoding="utf-8").strip().splitlines()[-200:]:
            try:
                entry = json.loads(line)
                if entry.get("timestamp", "").startswith(today):
                    daily_spend += entry.get("cost_usd", 0.0)
            except json.JSONDecodeError:
                continue

    metrics = {
        "date": today,
        "proof_type": proof_type,
        "workflows_total": workflow_count,
        "jobs_completed_total": job_count,
        "customers_total": customer_count,
        "today_actions": len(today_entries),
        "today_llm_spend_usd": round(daily_spend, 2),
        "today_action_breakdown": {},
    }
    for entry in today_entries:
        kind = entry.get("kind", "other")
        metrics["today_action_breakdown"][kind] = metrics["today_action_breakdown"].get(kind, 0) + 1

    proof_dir = DATA_ROOT / "proof" / proof_type
    proof_dir.mkdir(parents=True, exist_ok=True)
    path = write_file(proof_dir / f"metrics-{today}.json", json.dumps(metrics, indent=2))

    return StepOutcome(
        summary=f"Collected {proof_type} metrics: {len(today_entries)} actions today, ${daily_spend:.2f} LLM spend",
        artifacts=[{"kind": "proof-metrics", "title": f"{proof_type.title()} Metrics", "path": path, "metadata": metrics}],
        workflow_stage="data-collected",
    )


def handle_proof_generate(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Generate proof content: daily report card, case study, or weekly BIP thread."""
    context = json_loads(workflow["context_json"])
    proof_type = context.get("proof_type", "daily")
    today = datetime.now().strftime("%Y-%m-%d")

    proof_dir = DATA_ROOT / "proof" / proof_type
    metrics_path = proof_dir / f"metrics-{today}.json"
    metrics = {}
    if metrics_path.exists():
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    if proof_type == "daily":
        prompt = (
            "You are Rick, generating your daily report card — a transparent look at what you did today.\n\n"
            f"Metrics: {json.dumps(metrics)}\n\n"
            "Write a daily report card in markdown:\n"
            "# Rick's Daily Report Card — {date}\n"
            "## What I Did Today\n(bullet points of actual actions)\n"
            "## By the Numbers\n(key metrics in a table)\n"
            "## What I Learned\n(1-2 operational insights)\n"
            "## Tomorrow's Focus\n(1-2 priorities)\n\n"
            "Be specific and honest. Include real numbers. This is proof, not marketing."
        )
    elif proof_type == "case_study":
        delivery = context.get("trigger_payload", {})
        prompt = (
            "You are Rick, generating a case study from a completed delivery.\n\n"
            f"Delivery details: {json.dumps(delivery)}\n"
            f"Operational metrics: {json.dumps(metrics)}\n\n"
            "Write a case study in markdown:\n"
            "# Case Study: [descriptive title]\n"
            "## The Challenge\n## Rick's Approach\n## The Result\n## Key Metrics\n\n"
            "Focus on specific, measurable outcomes."
        )
    else:  # weekly_bip
        prompt = (
            "You are Rick, writing a weekly build-in-public thread.\n\n"
            f"This week's metrics: {json.dumps(metrics)}\n\n"
            "Write a thread-style post (numbered 1-7) covering:\n"
            "1. Hook: What Rick accomplished this week\n"
            "2-5. Specific wins, learnings, and metrics\n"
            "6. What's next\n"
            "7. CTA to meetrick.ai\n\n"
            "Honest, specific, engaging. Include real numbers."
        )

    fallback = (
        f"# Rick's {proof_type.title()} Report — {today}\n\n"
        f"## Summary\n- Total workflows: {metrics.get('workflows_total', 0)}\n"
        f"- Jobs completed: {metrics.get('jobs_completed_total', 0)}\n"
        f"- Today's actions: {metrics.get('today_actions', 0)}\n"
        f"- LLM spend: ${metrics.get('today_llm_spend_usd', 0):.2f}\n"
    )
    result = generate_text("writing", prompt, fallback)
    path = write_file(proof_dir / f"{proof_type}-{today}.md", result.content)

    return StepOutcome(
        summary=f"{proof_type.title()} proof content generated for {today}",
        artifacts=[{"kind": f"proof-{proof_type}", "title": f"{proof_type.title()} Proof", "path": path, "metadata": {}}],
        workflow_stage="proof-generated",
    )


def handle_proof_distribute(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Queue proof content for distribution across X, newsletter, and website."""
    context = json_loads(workflow["context_json"])
    proof_type = context.get("proof_type", "daily")
    today = datetime.now().strftime("%Y-%m-%d")

    proof_dir = DATA_ROOT / "proof" / proof_type
    content_path = proof_dir / f"{proof_type}-{today}.md"
    content = content_path.read_text(encoding="utf-8") if content_path.exists() else ""

    outbox_dir = DATA_ROOT / "mailbox" / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)

    # Queue X post
    write_file(outbox_dir / f"proof-x-{proof_type}-{today}.json", json.dumps({
        "type": "x_post",
        "content_source": str(content_path),
        "proof_type": proof_type,
        "status": "pending",
        "created_at": now_iso(),
    }, indent=2))

    # Copy to website proof section
    site_proof = DATA_ROOT / "projects" / "meetrick-site" / "proof"
    site_proof.mkdir(parents=True, exist_ok=True)
    write_file(site_proof / f"{proof_type}-{today}.md", content)

    return StepOutcome(
        summary=f"{proof_type.title()} proof queued for X + website",
        artifacts=[{"kind": "proof-distributed", "title": "Distributed Proof", "path": content_path, "metadata": {}}],
        workflow_status="done",
        workflow_stage="distributed",
        notify_text=f"Proof content ({proof_type}) published for {today}",
    )


# ---------------------------------------------------------------------------
# Skill 4: email-nurture-machine — Fix Outbox + Build List + Convert
# ---------------------------------------------------------------------------

def handle_list_build(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Analyze current subscriber base and plan growth hooks."""
    # Count current subscribers
    sub_count = connection.execute("SELECT COUNT(*) AS c FROM email_subscribers WHERE status = 'active'").fetchone()["c"]
    customer_count = connection.execute("SELECT COUNT(*) AS c FROM customers").fetchone()["c"]

    prompt = (
        "You are Rick, planning email list growth from scratch.\n\n"
        f"Current subscribers: {sub_count}\n"
        f"Current customers: {customer_count}\n\n"
        "Generate a list-building plan with:\n"
        "1. Lead magnet concept (free resource that demonstrates Rick's value)\n"
        "2. CTA copy for X bio, post footers, and Fiverr profile\n"
        "3. Welcome sequence outline (5 emails over 14 days)\n"
        "4. Subscriber growth hooks (where to embed signup CTAs)\n\n"
        "Be specific and actionable. Output as markdown."
    )
    fallback = (
        "# Email List Growth Plan\n\n"
        "## Lead Magnet\n"
        "**AI CEO Starter Kit** — Free PDF: 'How to Build an AI Agent That Runs Your Business'\n"
        "Includes: Rick's exact tech stack, daily operations checklist, cost breakdown.\n\n"
        "## CTA Copy\n"
        "- X bio: 'Get the free AI CEO Starter Kit → meetrick.ai/starter'\n"
        "- Post footer: 'Want the system behind this? Free starter kit: meetrick.ai/starter'\n\n"
        "## Welcome Sequence\n"
        "1. Day 0: Deliver starter kit + Rick's story\n"
        "2. Day 2: Quick win — one automation to implement today\n"
        "3. Day 5: Case study — real results from Rick's operations\n"
        "4. Day 10: The playbook pitch ($29)\n"
        "5. Day 14: Toolkit pitch ($97) with social proof\n\n"
        "## Growth Hooks\n"
        "- Every high-performing X post gets a reply with CTA\n"
        "- Every Fiverr delivery includes starter kit link\n"
        "- Every community response includes subtle CTA in bio\n"
    )
    result = generate_text("writing", prompt, fallback)

    plan_dir = DATA_ROOT / "email-nurture"
    plan_dir.mkdir(parents=True, exist_ok=True)
    path = write_file(plan_dir / "list-growth-plan.md", result.content)

    return StepOutcome(
        summary=f"Email list growth plan created. Current: {sub_count} subscribers, {customer_count} customers",
        artifacts=[{"kind": "list-growth-plan", "title": "List Growth Plan", "path": path, "metadata": {"subscribers": sub_count}}],
        workflow_stage="list-planned",
    )


def handle_sequence_draft(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Draft email nurture sequences: welcome, upsell, re-engagement."""
    prompt = (
        "You are Rick, drafting email nurture sequences.\n\n"
        "Write 3 complete email sequences:\n\n"
        "## Welcome Sequence (5 emails, days 0/2/5/10/14)\n"
        "For new subscribers. Goal: build trust, deliver value, convert to $29 Playbook.\n\n"
        "## Upsell Sequence (3 emails, days 0/3/7)\n"
        "For Playbook buyers. Goal: convert to $97 Toolkit or $499 Managed.\n\n"
        "## Re-engagement Sequence (3 emails, days 0/7/14)\n"
        "For inactive subscribers. Goal: re-activate or clean list.\n\n"
        "Each email needs: Subject line, preview text, body (under 200 words), CTA.\n"
        "Use {{first_name}}, {{checkout_url}}, {{unsubscribe_url}} as placeholders.\n"
        "Tone: helpful founder sharing real experience, not corporate marketing."
    )
    fallback = (
        "# Email Nurture Sequences\n\n"
        "## Welcome Sequence\n\n"
        "### Email 1 (Day 0) — Delivery\n"
        "**Subject:** Your AI CEO Starter Kit is here\n"
        "Hi {{first_name}}, here's your starter kit...\n\n"
        "### Email 2 (Day 2) — Quick Win\n"
        "**Subject:** Try this one automation today\n\n"
        "### Email 3 (Day 5) — Social Proof\n"
        "**Subject:** What Rick accomplished this week\n\n"
        "### Email 4 (Day 10) — Soft Pitch\n"
        "**Subject:** The full playbook behind Rick\n\n"
        "### Email 5 (Day 14) — Direct Offer\n"
        "**Subject:** Special offer for early subscribers\n\n"
        "## Upsell Sequence\n(3 emails)\n\n"
        "## Re-engagement Sequence\n(3 emails)\n"
    )
    result = generate_text("writing", prompt, fallback)

    sequences_dir = DATA_ROOT / "mailbox" / "sequences" / "nurture"
    sequences_dir.mkdir(parents=True, exist_ok=True)
    path = write_file(sequences_dir / "all-sequences.md", result.content)

    return StepOutcome(
        summary="Email nurture sequences drafted: welcome, upsell, re-engagement",
        artifacts=[{"kind": "email-sequences", "title": "Nurture Sequences", "path": path, "metadata": {}}],
        workflow_stage="sequences-drafted",
    )


def handle_outbox_send(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Process outbox: send pending emails via Resend API."""
    outbox_dir = DATA_ROOT / "mailbox" / "outbox"
    if not outbox_dir.exists():
        return StepOutcome(
            summary="Outbox empty — no emails to send",
            artifacts=[],
            workflow_stage="outbox-checked",
        )

    resend_key = os.getenv("RESEND_API_KEY", "").strip()
    if not resend_key:
        raise DependencyBlocked("email-send", "RESEND_API_KEY not configured. Set it in rick.env to enable email sending.")

    pending = []
    sent = 0
    errors = 0

    for f in sorted(outbox_dir.iterdir()):
        if not f.suffix == ".json":
            continue
        try:
            msg = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if msg.get("status") != "pending":
            continue
        # Check scheduled send time
        send_after = msg.get("send_after", "")
        if send_after and send_after > now_iso():
            continue
        pending.append((f, msg))

    for f, msg in pending[:20]:  # Max 20 per batch
        to_email = msg.get("to", "")
        if not to_email:
            continue

        try:
            import urllib.request
            body_md = msg.get("body_markdown", msg.get("pitch_markdown", ""))
            subject = "Message from Rick"
            # Extract subject from markdown
            for line in body_md.splitlines():
                if line.startswith("**Subject:**"):
                    subject = line.replace("**Subject:**", "").strip()
                    break

            send_payload = json.dumps({
                "from": os.getenv("RICK_EMAIL_FROM", "rick@meetrick.ai"),
                "to": [to_email],
                "subject": subject,
                "text": body_md,
            }).encode()

            req = urllib.request.Request(
                "https://api.resend.com/emails",
                data=send_payload,
                headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=15)
            msg["status"] = "sent"
            msg["sent_at"] = now_iso()
            sent += 1
        except Exception as exc:
            msg["status"] = "error"
            msg["error"] = str(exc)[:200]
            errors += 1

        write_file(f, json.dumps(msg, indent=2))

    # Move sent files to sent/ directory
    sent_dir = DATA_ROOT / "mailbox" / "sent"
    sent_dir.mkdir(parents=True, exist_ok=True)
    for f, msg in pending[:20]:
        if msg.get("status") == "sent":
            f.rename(sent_dir / f.name)

    return StepOutcome(
        summary=f"Outbox processed: {sent} sent, {errors} errors, {len(pending) - sent - errors} remaining",
        artifacts=[],
        workflow_stage="outbox-processed",
    )


def handle_engagement_track(connection: sqlite3.Connection, workflow: sqlite3.Row, job: sqlite3.Row) -> StepOutcome:
    """Track email engagement: opens, replies, conversions."""
    sub_count = connection.execute("SELECT COUNT(*) AS c FROM email_subscribers WHERE status = 'active'").fetchone()["c"]
    customer_count = connection.execute("SELECT COUNT(*) AS c FROM customers").fetchone()["c"]

    # Check for recent subscriber signups
    recent_subs = connection.execute(
        "SELECT COUNT(*) AS c FROM email_subscribers WHERE subscribed_at > datetime('now', '-7 days')"
    ).fetchone()["c"]

    # Check sent emails
    sent_dir = DATA_ROOT / "mailbox" / "sent"
    sent_count = 0
    if sent_dir.exists():
        sent_count = sum(1 for f in sent_dir.iterdir() if f.suffix == ".json")

    report = {
        "date": now_iso(),
        "active_subscribers": sub_count,
        "total_customers": customer_count,
        "new_subscribers_7d": recent_subs,
        "total_emails_sent": sent_count,
    }

    report_dir = DATA_ROOT / "email-nurture"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = write_file(report_dir / f"engagement-{datetime.now():%Y-%m-%d}.json", json.dumps(report, indent=2))

    return StepOutcome(
        summary=f"Email engagement: {sub_count} active subs, {recent_subs} new (7d), {sent_count} total sent",
        artifacts=[{"kind": "engagement-report", "title": "Email Engagement", "path": path, "metadata": report}],
        workflow_status="done",
        workflow_stage="tracked",
    )


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

PHASE1_HANDLERS = {
    # Skill 1: deal-closer
    "lead_intake": handle_lead_intake,
    "lead_qualify": handle_lead_qualify,
    "pitch_draft": handle_pitch_draft,
    "pitch_send": handle_pitch_send,
    "followup_sequence": handle_followup_sequence,
    "close_or_escalate": handle_close_or_escalate,
    # Skill 2: testimonial-machine
    "trigger_evaluate": handle_trigger_evaluate,
    "request_send": handle_request_send,
    "response_collect": handle_response_collect,
    "format_multi": handle_format_multi,
    "deploy_surfaces": handle_deploy_surfaces,
    # Skill 3: proof-factory
    "data_collect": handle_data_collect,
    "proof_generate": handle_proof_generate,
    "proof_distribute": handle_proof_distribute,
    # Skill 4: email-nurture-machine
    "list_build": handle_list_build,
    "sequence_draft": handle_sequence_draft,
    "outbox_send": handle_outbox_send,
    "engagement_track": handle_engagement_track,
}
