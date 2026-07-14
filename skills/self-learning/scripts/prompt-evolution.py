#!/usr/bin/env python3
"""
prompt-evolution.py — Self-modifying prompt system for Rick
Runs Sunday 8pm (before war room).

Actions:
1. Read signal-tracker weekly rollup → extract best/worst content types
2. Read last 7 retros → extract lessons about what to change
3. Read experiment outcomes → extract patterns
4. Generate updated prompts for content-engine.sh (morning/midday/evening slots)
5. Version-control prompts: active.md → history, new → active
6. Disable dead crons (HEARTBEAT_OK > 90% of runs)

Versioned prompts live in: ~/rick-vault/prompts/<name>/
  active.md      — current live prompt
  challenger.md  — A/B test challenger (if any)
  history.jsonl  — audit log of all changes
"""

from __future__ import annotations
import json
import os
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
WORKSPACE = Path(os.getenv("RICK_WORKSPACE_ROOT", str(Path.home() / ".openclaw/workspace")))
NOW = datetime.now().isoformat()
TODAY = date.today().isoformat()
PROMPTS_DIR = DATA_ROOT / "prompts"
LOG_DIR = DATA_ROOT / "logs/cron"

def ask_claude(prompt: str, model: str = "claude-sonnet-4-6") -> str:
    try:
        result = subprocess.run(
            ["claude", "--print", "--model", model],
            input=prompt, capture_output=True, text=True, timeout=90
        )
        return result.stdout.strip()
    except Exception:
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

def load_prompt(name: str) -> str:
    path = PROMPTS_DIR / name / "active.md"
    return path.read_text() if path.exists() else ""

def save_prompt(name: str, new_prompt: str, reason: str) -> None:
    prompt_dir = PROMPTS_DIR / name
    prompt_dir.mkdir(parents=True, exist_ok=True)
    active = prompt_dir / "active.md"
    history = prompt_dir / "history.jsonl"

    old_prompt = active.read_text() if active.exists() else ""
    if old_prompt == new_prompt:
        print(f"[prompt-evolution] {name}: no change.")
        return

    # Archive old
    entry = {
        "timestamp": NOW,
        "reason": reason,
        "old_hash": hash(old_prompt),
        "new_hash": hash(new_prompt),
        "old_preview": old_prompt[:200],
        "new_preview": new_prompt[:200],
    }
    with open(history, "a") as f:
        f.write(json.dumps(entry) + "\n")

    active.write_text(new_prompt)
    print(f"[prompt-evolution] {name}: updated.")

def get_signal_context() -> str:
    """Read signal tracker for content performance context."""
    tracker = DATA_ROOT / "projects/x-twitter/signal-tracker.json"
    if not tracker.exists():
        return "No signal data yet."
    try:
        data = json.loads(tracker.read_text())
        rollups = data.get("weekly_rollups", [])
        if not rollups:
            return "No weekly rollups yet."
        latest = rollups[-1]
        bias = latest.get("queue_bias", {})
        types = latest.get("types", [])
        type_summary = "\n".join(
            f"  - {t['type']}: score={t['type_score']:.4f}, posts={t['posts']}, followers_gained={t['followers_gained']}"
            for t in sorted(types, key=lambda x: x["type_score"], reverse=True)
            if t["posts"] > 0
        )
        return (
            f"Week: {latest.get('week_start')} to {latest.get('week_end')}\n"
            f"Winner: {bias.get('winner_type')} | Loser: {bias.get('loser_type')}\n"
            f"Content type performance:\n{type_summary}\n"
            f"Recommendation: {bias.get('recommendation')}"
        )
    except Exception as e:
        return f"Signal read error: {e}"

def get_retro_lessons() -> str:
    """Pull key lessons from last 7 daily retros."""
    retro_dir = DATA_ROOT / "reflections"
    if not retro_dir.exists():
        return "No retros yet."
    retros = sorted(retro_dir.glob("*.md"))[-7:]
    combined = ""
    for r in retros:
        combined += r.read_text()[:600] + "\n---\n"
    return combined[:3000] if combined else "No retro content found."

def get_experiment_lessons() -> str:
    """Pull succeeded + failed experiment patterns."""
    patterns_dir = DATA_ROOT / "learning/patterns"
    if not patterns_dir.exists():
        return "No experiment patterns yet."
    files = sorted(patterns_dir.glob("*.md"))[-10:]
    combined = "\n".join(f.read_text()[:400] for f in files)
    return combined[:2000] if combined else "No patterns yet."

def get_current_prompt_context() -> str:
    """Read existing content engine prompt bias."""
    bias_path = DATA_ROOT / "prompts/x-content-bias.json"
    if bias_path.exists():
        try:
            return json.dumps(json.loads(bias_path.read_text()), indent=2)
        except Exception:
            pass
    return "No bias file yet."

def evolve_morning_prompt(signal_ctx: str, retro_ctx: str, exp_ctx: str) -> str:
    current = load_prompt("x-morning")
    prompt = f"""You are evolving Rick's morning X post prompt to maximize revenue toward $100K MRR.

CURRENT PROMPT (if any):
{current[:1000] if current else 'None — create from scratch'}

SIGNAL DATA (what's working):
{signal_ctx}

RECENT RETRO LESSONS:
{retro_ctx}

EXPERIMENT LEARNINGS:
{exp_ctx}

TASK: Write an improved prompt for Rick's morning X post (8am PT slot).
The prompt will be passed to Claude claude-haiku-4-5 to generate the actual tweet.

Rules for the prompt:
- Push toward the winning content type from signal data
- Include specific instructions about tone, angle, CTA
- End with: "CTA should drive to https://meetrick.ai/ or ask a question that starts a conversation"
- No more than 400 words
- Be specific about what NOT to do (loser content type)

Return ONLY the prompt text, no explanation."""

    return ask_claude(prompt)

def evolve_evening_prompt(signal_ctx: str, retro_ctx: str, exp_ctx: str) -> str:
    current = load_prompt("x-evening")
    prompt = f"""You are evolving Rick's evening X post prompt to maximize revenue toward $100K MRR.

CURRENT PROMPT (if any):
{current[:1000] if current else 'None — create from scratch'}

SIGNAL DATA (what's working):
{signal_ctx}

RECENT RETRO LESSONS:
{retro_ctx}

TASK: Write an improved prompt for Rick's evening X post (6pm PT slot).
Evening slot is best for: engagement bait, open questions, progress updates.
The prompt will be passed to Claude claude-haiku-4-5.

Rules:
- Favor real_number and counterintuitive types in evening
- Must include an invitation to reply (builds conversation)
- No hard sell in evening — build relationship
- Max 400 words

Return ONLY the prompt text."""

    return ask_claude(prompt)

def detect_dead_crons() -> list[str]:
    """Find crons that produce HEARTBEAT_OK >90% of the time — candidates for disabling."""
    if not LOG_DIR.exists():
        return []
    dead = []
    for log_file in LOG_DIR.glob("*.log"):
        try:
            lines = log_file.read_text().splitlines()
            recent = lines[-50:] if len(lines) > 50 else lines
            if len(recent) < 10:
                continue
            ok_count = sum(1 for l in recent if "HEARTBEAT_OK" in l or "no work" in l.lower() or "nothing to do" in l.lower())
            ratio = ok_count / len(recent)
            if ratio > 0.9:
                dead.append(f"{log_file.stem}: {ok_count}/{len(recent)} runs were empty ({ratio:.0%})")
        except Exception:
            pass
    return dead

def main() -> None:
    print("[prompt-evolution] Starting weekly prompt evolution...")

    signal_ctx = get_signal_context()
    retro_ctx = get_retro_lessons()
    exp_ctx = get_experiment_lessons()

    print("[prompt-evolution] Evolving morning prompt...")
    morning_prompt = evolve_morning_prompt(signal_ctx, retro_ctx, exp_ctx)
    if morning_prompt:
        save_prompt("x-morning", morning_prompt, f"Weekly evolution {TODAY}: {signal_ctx[:100]}")

    print("[prompt-evolution] Evolving evening prompt...")
    evening_prompt = evolve_evening_prompt(signal_ctx, retro_ctx, exp_ctx)
    if evening_prompt:
        save_prompt("x-evening", evening_prompt, f"Weekly evolution {TODAY}: retro lessons applied")

    dead = detect_dead_crons()
    dead_report = ""
    if dead:
        dead_report = "\n\n⚠️ **Dead cron candidates:**\n" + "\n".join(f"- {d}" for d in dead)
        print(f"[prompt-evolution] Dead crons: {len(dead)}")

    summary = (
        f"🔄 **Prompt Evolution — {TODAY}**\n"
        f"Signals read: {'✅' if signal_ctx else '❌'}\n"
        f"Retros read: {'✅' if retro_ctx else '❌'}\n"
        f"Experiments read: {'✅' if exp_ctx else '❌'}\n"
        f"Prompts updated: morning + evening\n"
        f"Winner content type: {signal_ctx.split('Winner:')[1].split('|')[0].strip() if 'Winner:' in signal_ctx else 'unknown'}"
        + dead_report
    )
    send_telegram("ceo-hq", summary)
    print("[prompt-evolution] Done.")

if __name__ == "__main__":
    main()
