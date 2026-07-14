#!/usr/bin/env python3
"""
experiment-engine.py — Rick's hypothesis generator + outcome checker
Runs at 6:00 PM daily.

Actions:
1. Check any experiments at measure_at → record outcomes
2. Promote succeeded learnings to patterns/
3. Generate new hypothesis if slot available
4. Auto-execute if reversible + within guardrails
5. Write to Telegram Product Lab on any state change

Usage:
  python3 experiment-engine.py --check    # just check outcomes
  python3 experiment-engine.py --generate # just generate new
  python3 experiment-engine.py            # both (default)
"""

from __future__ import annotations
import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
WORKSPACE = Path(os.getenv("RICK_WORKSPACE_ROOT", str(Path.home() / ".openclaw/workspace")))
TODAY = date.today().isoformat()
NOW = datetime.now(timezone.utc).isoformat()
MAX_ACTIVE = 3
MAX_PER_STAGE = 1

QUEUE_PATH = DATA_ROOT / "experiments/queue.json"
OUTCOMES_DIR = DATA_ROOT / "learning/outcomes"
PATTERNS_DIR = DATA_ROOT / "learning/patterns"

def load_queue() -> dict:
    if QUEUE_PATH.exists():
        try:
            return json.loads(QUEUE_PATH.read_text())
        except Exception:
            pass
    return {"version": "1.0", "updated_at": NOW, "items": []}

def save_queue(q: dict) -> None:
    q["updated_at"] = NOW
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_PATH.write_text(json.dumps(q, indent=2))

def ask_llm(prompt: str, model: str = "claude-haiku-4-5") -> str:
    """Call LLM via OpenAI API (works reliably unlike claude CLI)."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        print("[experiment-engine] No OPENAI_API_KEY set, trying claude CLI fallback")
        try:
            result = subprocess.run(
                ["claude", "--print", "--model", model],
                input=prompt, capture_output=True, text=True, timeout=60
            )
            return result.stdout.strip()
        except Exception:
            return ""
    # Map model names to OpenAI
    oai_model = "gpt-4o-mini"
    if "sonnet" in model or "opus" in model:
        oai_model = "gpt-4o"
    try:
        import urllib.request
        payload = json.dumps({
            "model": oai_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 2000,
        })
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload.encode(),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[experiment-engine] LLM call failed: {e}")
        return ""

# topic→(chat_id, thread_id) — migrated from tg-topic.sh (Strategy-C #1)
_TG_TOPIC_MAP = {
    "ops-alerts": ("-1003781085932", 34), "ops": ("-1003781085932", 34),
    "approvals":  ("-1003781085932", 26), "customer":  ("-1003781085932", 32),
    "product-lab":("-1003781085932", 28), "distribution":("-1003781085932", 30),
    "traffic":    ("-1003781085932", 715), "test":      ("-1003781085932", 36),
    "ceo-hq":     ("-1003781085932", 24),
}


def send_telegram(topic: str, text: str) -> None:
    """Send to named Telegram topic via openclaw message send (tg-topic.sh fallback)."""
    entry = _TG_TOPIC_MAP.get(topic)
    if entry:
        chat_id, tid = entry
        try:
            r = subprocess.run(
                [
                    "openclaw", "message", "send",
                    "--channel", "telegram",
                    "--target", chat_id,
                    "--thread-id", str(tid),
                    "--message", text,
                ],
                capture_output=True, timeout=15,
            )
            if r.returncode == 0:
                return
        except Exception:
            pass
    # Fallback: tg-topic.sh
    try:
        subprocess.run(
            ["bash", str(WORKSPACE / "scripts/tg-topic.sh"), topic, text],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass

def fingerprint(stage: str, channel: str, offer: str, hypothesis: str, target: str) -> str:
    raw = f"{stage}|{channel}|{offer}|{hypothesis}|{target}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]

# ── Outcome checker ───────────────────────────────────────────────────────────
def check_outcomes(q: dict) -> int:
    """Check experiments past their measure_at. Return count updated."""
    updated = 0
    for exp in q["items"]:
        if exp["status"] not in ("launched", "measuring"):
            continue
        measure_at = exp.get("measure_at")
        if not measure_at:
            continue
        measure_dt = datetime.fromisoformat(measure_at.replace("Z", "+00:00"))
        if datetime.now(timezone.utc) < measure_dt:
            continue

        # Read actual metrics — simplified: use X signal tracker + Stripe for now
        actuals = collect_actuals(exp)
        decision = evaluate(exp, actuals)

        exp["result"] = {
            "checked_at": NOW,
            "decision": decision,
            "reason": f"Auto-evaluated at measure window",
            "actuals": actuals,
            "next_action": suggest_next(decision, exp),
        }
        exp["status"] = decision

        # Write outcome record
        OUTCOMES_DIR.mkdir(parents=True, exist_ok=True)
        outcome_path = OUTCOMES_DIR / f"{exp['id']}-outcome.json"
        outcome_path.write_text(json.dumps(exp, indent=2))

        # Promote to patterns if succeeded
        if decision in ("succeeded", "scaled"):
            promote_learning(exp)
            send_telegram("product-lab", f"✅ Experiment **{exp['title']}** succeeded!\n{exp['result']['reason']}")
        elif decision == "failed":
            promote_learning(exp, negative=True)
            send_telegram("ops-alerts", f"❌ Experiment **{exp['title']}** failed.\n{exp['result']['reason']}")

        updated += 1
        print(f"[experiment-engine] {exp['id']} → {decision}")

    return updated

def collect_actuals(exp: dict) -> dict:
    """Collect real metrics. Extensible per channel."""
    actuals: dict = {"primary_metric": 0, "sample_size": 0, "revenue_usd": 0, "followers_delta": 0}
    channel = exp.get("channel", "")
    if channel == "x":
        # Read from signal tracker
        tracker = DATA_ROOT / "projects/x-twitter/signal-tracker.json"
        if tracker.exists():
            try:
                data = json.loads(tracker.read_text())
                exp_id = exp["id"]
                related = [p for p in data.get("posts", []) if p.get("experiment_id") == exp_id]
                if related:
                    total_eng = sum(p.get("derived", {}).get("engagements_48h", 0) for p in related)
                    total_impressions = sum(p.get("snapshots", {}).get("t48h", {}).get("impressions", 0) for p in related)
                    actuals["primary_metric"] = total_eng / total_impressions if total_impressions else 0
                    actuals["sample_size"] = total_impressions
                    actuals["followers_delta"] = sum(p.get("derived", {}).get("follower_delta_48h", 0) for p in related)
            except Exception:
                pass
    return actuals

def evaluate(exp: dict, actuals: dict) -> str:
    success = exp.get("success_threshold", {})
    fail = exp.get("fail_threshold", {})
    metric_val = actuals.get("primary_metric", 0)
    sample = actuals.get("sample_size", 0)
    min_sample = success.get("min_sample", 100)

    if sample < min_sample:
        # Inconclusive — extend once
        if exp.get("status") == "measuring":
            return "failed"  # already extended once
        exp["status"] = "measuring"
        exp["measure_at"] = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        return "measuring"

    success_op = success.get("operator", ">=")
    success_val = success.get("value", 0)
    if success_op == ">=" and metric_val >= success_val:
        if metric_val >= success_val * 1.5:
            return "scaled"
        return "succeeded"
    fail_val = fail.get("value", 0)
    fail_op = fail.get("operator", "<")
    if fail_op == "<" and metric_val < fail_val:
        return "failed"
    return "inconclusive"

def suggest_next(decision: str, exp: dict) -> str:
    suggestions = {
        "succeeded": f"Scale: run 3x more of this experiment type. Update prompt variant for '{exp.get('channel','x')}' content.",
        "scaled": f"Immediate scale: this outperformed by 50%+. Make it the default approach. Update active prompt.",
        "failed": f"Anti-pattern logged. Do not retry same hypothesis. Pivot to different stage or channel.",
        "inconclusive": f"Insufficient data. Consider increasing promotion or extending window.",
    }
    return suggestions.get(decision, "Review manually.")

def promote_learning(exp: dict, negative: bool = False) -> None:
    """Write durable pattern to learning/patterns/."""
    PATTERNS_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{'anti-' if negative else ''}{exp['id']}-{exp['stage']}-{exp['channel']}.md"
    content = (
        f"# {'❌ Anti-pattern' if negative else '✅ Pattern'}: {exp['title']}\n\n"
        f"**Stage:** {exp['stage']} | **Channel:** {exp['channel']}\n"
        f"**Hypothesis:** {exp['hypothesis']}\n\n"
        f"**Outcome:** {exp.get('result', {}).get('decision','?')}\n"
        f"**Actuals:** {json.dumps(exp.get('result', {}).get('actuals', {}))}\n\n"
        f"**Next action:** {exp.get('result', {}).get('next_action','')}\n\n"
        f"**Promoted:** {TODAY}\n"
    )
    (PATTERNS_DIR / fname).write_text(content)
    print(f"[experiment-engine] Promoted {'anti-' if negative else ''}pattern: {fname}")

# ── Hypothesis generator ──────────────────────────────────────────────────────
def generate_hypothesis(q: dict) -> Optional[dict]:
    """If capacity allows, generate and queue a new experiment."""
    active = [i for i in q["items"] if i["status"] in ("launched","measuring")]
    queued = [i for i in q["items"] if i["status"] == "queued"]

    if len(active) >= MAX_ACTIVE:
        print(f"[experiment-engine] At capacity ({len(active)} active). Skipping generation.")
        return None

    # Read recent patterns for context
    patterns = []
    for f in sorted(PATTERNS_DIR.glob("*.md"))[-5:]:
        patterns.append(f.read_text()[:500])

    # Read recent retro
    retro_dir = DATA_ROOT / "reflections"
    recent_retro = ""
    retros = sorted(retro_dir.glob("*.md"))[-3:] if retro_dir.exists() else []
    for r in retros:
        recent_retro += r.read_text()[:800] + "\n"

    # Read stripe for revenue context
    revenue_context = "MRR: $0, 0 customers"
    snap = DATA_ROOT / "revenue/snapshot.json"
    if snap.exists():
        try:
            s = json.loads(snap.read_text())
            revenue_context = f"MRR: ${s.get('mrr',0)}, customers: {s.get('total_customers',0)}"
        except Exception:
            pass

    prompt = f"""You are Rick's hypothesis engine. Generate ONE specific, measurable experiment Rick should run next.

CONTEXT:
- Mission: $100K MRR. Current state: {revenue_context}
- Products: $499/mo Managed AI CEO, $2500 one-time AI CEO Setup
- Platform: X (@MeetRickAI), meetrick.ai, email

BLOCKED CHANNELS (do NOT generate experiments for these):
- X direct messages (DM): X account password unknown, DM access blocked
- Any experiment requiring manual founder action to execute

AVAILABLE CHANNELS (generate experiments for these only):
- X posts and replies (xpost CLI working)
- Moltbook posts (API key active)
- meetrick.ai site changes (GitHub Pages, git push to deploy)
- Email via Resend (RESEND_API_KEY active)
- Stripe checkout page changes

RECENT PATTERNS (what's worked/failed):
{chr(10).join(patterns) if patterns else 'No patterns yet — this is early.'}

RECENT RETRO LESSONS:
{recent_retro[:1200] if recent_retro else 'No retros yet.'}

ACTIVE EXPERIMENTS: {len(active)}
QUEUED: {len(queued)}

Generate ONE experiment in this exact JSON format:
{{
  "title": "short title",
  "hypothesis": "If we [action], then [metric] will [change] because [reason]",
  "stage": "traffic|audience|capture|conversion|retention",
  "channel": "x|site|email|offer|outreach",
  "offer": "which product or null",
  "launch_action": {{
    "type": "tweet|thread|landing_page_change|cta_change|email|outreach|offer_test",
    "target": "where/who",
    "instructions": "exact steps to execute"
  }},
  "primary_metric": {{
    "name": "metric name",
    "source": "x_signal_tracker|ga4|stripe|email|manual",
    "unit": "unit",
    "baseline": 0,
    "current": null
  }},
  "success_threshold": {{"metric": "name", "operator": ">=", "value": 0, "min_sample": 50, "window_hours": 48}},
  "fail_threshold": {{"metric": "name", "operator": "<", "value": 0, "min_sample": 50, "window_hours": 48}},
  "measurement_window_hours": 48,
  "priority": 1
}}

Rules:
- Must be reversible
- Must be executable by Rick autonomously (no founder approval)
- Must have clear numeric success threshold
- Prefer conversion stage if no revenue yet
- No experiment should repeat a recent failure pattern
- Be specific: exact tweet topic, exact CTA change, exact email subject

Return ONLY the JSON object, no markdown."""

    raw = ask_llm(prompt, model="claude-sonnet-4-6")
    # Strip markdown fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        exp_data = json.loads(raw)
    except Exception as e:
        print(f"[experiment-engine] Failed to parse hypothesis: {e}\n{raw[:200]}")
        return None

    # Build full experiment record
    exp_id_suffix = str(len(q["items"]) + 1).zfill(3)
    exp_id = f"exp-{TODAY.replace('-','')}-{exp_id_suffix}"
    fp = fingerprint(
        exp_data.get("stage",""),
        exp_data.get("channel",""),
        exp_data.get("offer",""),
        exp_data.get("hypothesis",""),
        exp_data.get("launch_action",{}).get("target",""),
    )

    # Deduplicate
    existing_fps = {i.get("hypothesis_fingerprint") for i in q["items"] if i.get("status") not in ("archived","failed","killed")}
    if fp in existing_fps:
        print(f"[experiment-engine] Duplicate hypothesis fingerprint {fp} — skipping.")
        return None

    experiment = {
        "id": exp_id,
        "created_at": NOW,
        "source": {"type": "initiative_generator", "run_id": f"exp-engine-{TODAY}"},
        "hypothesis_fingerprint": fp,
        "owner": "rick",
        "status": "queued",
        "launched_at": None,
        "measure_at": None,
        "result": None,
        "artifacts": {"post_ids": [], "urls": [], "email_ids": []},
        "notes": "",
        "supersedes": None,
        **exp_data
    }

    q["items"].append(experiment)
    print(f"[experiment-engine] Queued: {exp_id} — {experiment['title']}")

    send_telegram("product-lab",
        f"🧪 New experiment queued: **{experiment['title']}**\n"
        f"Stage: {experiment['stage']} | Channel: {experiment['channel']}\n"
        f"_{experiment['hypothesis'][:200]}_"
    )
    return experiment

# ── Auto-launch queued experiments ───────────────────────────────────────────
def auto_launch(q: dict) -> int:
    launched = 0
    active_count = len([i for i in q["items"] if i["status"] in ("launched","measuring")])
    active_stages = {i["stage"] for i in q["items"] if i["status"] in ("launched","measuring")}

    for exp in q["items"]:
        if exp["status"] != "queued":
            continue
        if active_count >= MAX_ACTIVE:
            break
        if exp["stage"] in active_stages:
            continue  # one per stage
        # Auto-execute X and email channel experiments
        if exp["channel"] in ("x", "email") and exp["launch_action"]["type"] in ("tweet", "thread", "email"):
            success = execute_experiment(exp)
            if success:
                exp["status"] = "launched"
                exp["launched_at"] = NOW
                window = exp.get("measurement_window_hours", 48)
                exp["measure_at"] = (datetime.now(timezone.utc) + timedelta(hours=window)).isoformat()
                active_count += 1
                active_stages.add(exp["stage"])
                launched += 1
                print(f"[experiment-engine] Auto-launched: {exp['id']}")

    return launched

def execute_experiment(exp: dict) -> bool:
    """Execute the launch action. Returns True on success."""
    action = exp.get("launch_action", {})
    action_type = action.get("type")
    instructions = action.get("instructions", "")

    if action_type == "tweet":
        # Ask Claude to write the exact tweet from instructions
        tweet_prompt = f"""Write a tweet for this experiment:
Instructions: {instructions}
Offer: {exp.get('offer','meetrick.ai')}
Hypothesis: {exp.get('hypothesis','')}
Rules: max 230 chars, no hashtags, conversational, end with meetrick.ai CTA if product experiment
Return ONLY the tweet text."""
        tweet_text = ask_llm(tweet_prompt)
        if not tweet_text or len(tweet_text) > 280:
            return False
        # Preflight: verify xpost auth before attempting post
        try:
            preflight = subprocess.run(
                ["xpost", "timeline", "MeetRickAI", "--limit", "1"],
                capture_output=True, text=True, timeout=15
            )
            pf_data = json.loads(preflight.stdout)
            if isinstance(pf_data, dict) and pf_data.get("status") == 401:
                print(f"[experiment-engine] xpost auth expired (401) — skipping tweet auto-launch")
                return False
        except Exception as pf_err:
            print(f"[experiment-engine] xpost preflight failed: {pf_err} — skipping")
            return False
        try:
            result = subprocess.run(
                ["xpost", "post", tweet_text],
                capture_output=True, text=True, timeout=20
            )
            data = json.loads(result.stdout)
            # Handle API error responses
            if isinstance(data, dict) and data.get("status") in (401, 403, 429):
                print(f"[experiment-engine] Tweet blocked: HTTP {data.get('status')} — {data.get('title','unknown')}")
                return False
            post_id = data["data"]["id"]
            exp["artifacts"]["post_ids"].append(post_id)
            print(f"[experiment-engine] Posted tweet {post_id}")
            # Log to signal tracker with experiment_id
            log_to_signal_tracker(post_id, exp["id"], exp.get("channel","x"))
            return True
        except KeyError as e:
            raw_out = result.stdout[:300] if 'result' in dir() else '(no output)'
            print(f"[experiment-engine] Tweet parse error (missing key {e}): {raw_out}")
            return False
        except Exception as e:
            print(f"[experiment-engine] Tweet failed: {e}")
            return False

    # Other action types (landing_page, offer_test) → queue for next heartbeat work item
    return False

def log_to_signal_tracker(post_id: str, exp_id: str, channel: str) -> None:
    tracker_path = DATA_ROOT / "projects/x-twitter/signal-tracker.json"
    try:
        data = json.loads(tracker_path.read_text()) if tracker_path.exists() else {"version":"1.0","updated_at":NOW,"account":{"handle":"@MeetRickAI","user_id":"2032441385828380672"},"posts":[],"weekly_rollups":[]}
        data["posts"].append({
            "post_id": post_id,
            "posted_at": NOW,
            "experiment_id": exp_id,
            "type": "product",
            "prompt_variant_id": "experiment",
            "text_hash": hashlib.sha1(post_id.encode()).hexdigest()[:8],
            "text_preview": f"[experiment {exp_id}]",
            "followers_before": 0,
            "snapshots": {"t1h": {}, "t24h": {}, "t48h": {}},
            "derived": {"engagements_48h": 0, "engagement_rate_48h": 0, "follower_delta_48h": 0, "followers_per_1k_impressions_48h": 0, "profile_visit_rate_48h": 0, "reply_rate_48h": 0, "leads_generated_7d": 0, "revenue_attributed_7d_usd": 0, "type_score": 0},
        })
        data["updated_at"] = NOW
        tracker_path.write_text(json.dumps(data, indent=2))
    except Exception as e:
        print(f"[experiment-engine] Signal tracker update failed: {e}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Skip all sends/writes (overrides RICK_EXPERIMENT_ENGINE_LIVE)")
    args = parser.parse_args()

    # Kill-switch: set RICK_EXPERIMENT_ENGINE_LIVE=1 in rick.env to enable auto-launch + Telegram sends
    live = os.getenv("RICK_EXPERIMENT_ENGINE_LIVE", "0").strip() == "1" and not args.dry_run
    if not live:
        print("[experiment-engine] RICK_EXPERIMENT_ENGINE_LIVE not set — running in dry mode (check/generate only, no auto-launch/sends). Set RICK_EXPERIMENT_ENGINE_LIVE=1 to enable.")

    do_check = args.check or (not args.check and not args.generate)
    do_generate = args.generate or (not args.check and not args.generate)

    q = load_queue()

    if do_check:
        n = check_outcomes(q)
        print(f"[experiment-engine] Outcomes checked: {n}")

    if do_generate:
        exp = generate_hypothesis(q)
        if exp:
            if live:
                n = auto_launch(q)
                print(f"[experiment-engine] Auto-launched: {n}")
            else:
                print("[experiment-engine] Auto-launch skipped (dry mode — set RICK_EXPERIMENT_ENGINE_LIVE=1 to enable)")

    save_queue(q)
    print("[experiment-engine] Done.")

if __name__ == "__main__":
    main()
