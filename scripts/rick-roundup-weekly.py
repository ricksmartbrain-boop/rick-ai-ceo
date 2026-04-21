#!/usr/bin/env python3
"""Weekly Rick Roundup — Sunday 9am email to the Resend Pro audience.

Summarizes the fleet in a fun, personality-forward tone. Uses Resend's
Broadcasts API (create + send) so rate-limiting + per-contact delivery is
handled server-side.

Dry-run by default — set RICK_ROUNDUP_LIVE=1 in rick.env (or pass --force)
to actually send.

Usage:
    python3 rick-roundup-weekly.py --dry-run   # default; compose, do not send
    python3 rick-roundup-weekly.py --force     # send even without RICK_ROUNDUP_LIVE=1
"""

from __future__ import annotations

import argparse
import datetime
import html as _html_mod
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path


ENV_FILE = Path.home() / "clawd" / "config" / "rick.env"
LOG_FILE = Path.home() / "rick-vault" / "operations" / "rick-roundup-weekly.jsonl"
FLEET_URL = "https://api.meetrick.ai/api/v1/fleet/public"
RESEND_BROADCASTS = "https://api.resend.com/broadcasts"
DEFAULT_FROM = "Rick <rick@meetrick.ai>"
DB_FILE = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault"))) / "runtime" / "rick-runtime.db"
HIVE_ID_FILE = Path.home() / ".openclaw" / ".hive-id"


def _open_db() -> sqlite3.Connection | None:
    try:
        if not DB_FILE.exists():
            return None
        c = sqlite3.connect(str(DB_FILE), timeout=5.0)
        c.row_factory = sqlite3.Row
        return c
    except Exception:
        return None


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _esc(text: str) -> str:
    return _html_mod.escape(str(text)[:400], quote=True)


def _truncate(text: str, limit: int = 200) -> str:
    text = str(text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _read_callsign() -> str | None:
    if HIVE_ID_FILE.exists():
        try:
            val = HIVE_ID_FILE.read_text(encoding="utf-8").strip()
            if val:
                return val
        except OSError:
            pass
    return None


def _section_learnings(conn: sqlite3.Connection) -> tuple[str, str]:
    """Top 3 dream_insight patterns Rick learned this week."""
    if not _table_exists(conn, "effective_patterns"):
        return "", ""
    try:
        rows = conn.execute(
            """
            SELECT snippet, evidence_json
              FROM effective_patterns
             WHERE pattern_kind = 'dream_insight'
             ORDER BY created_at DESC
             LIMIT 3
            """
        ).fetchall()
    except Exception:
        return "", ""
    if not rows:
        return "", ""
    html_items, text_items = [], []
    for row in rows:
        snippet = _truncate(row["snippet"] or "", 200)
        day = ""
        try:
            ev = json.loads(row["evidence_json"] or "{}")
            day = ev.get("dream_day", "")
        except (json.JSONDecodeError, TypeError):
            pass
        src = f" <span style='color:#999;font-size:12px;'>— dream on {_esc(day)}</span>" if day else ""
        html_items.append(f"<li>{_esc(snippet)}{src}</li>")
        text_items.append(f"• {snippet}" + (f"  (dream on {day})" if day else ""))
    html = (
        "<h3 style=\"margin:24px 0 8px;font-size:16px;\">What Rick learned this week</h3>"
        f"<ul style=\"padding-left:20px;margin:0;line-height:1.7;\">{''.join(html_items)}</ul>"
    )
    text = "\nWhat Rick learned this week:\n" + "\n".join(text_items) + "\n"
    return html, text


def _section_top_skills(conn: sqlite3.Connection) -> tuple[str, str]:
    """Top 5 skills by ROI (quality/cost) in last 7 days."""
    if not _table_exists(conn, "outcomes"):
        return "", ""
    try:
        rows = conn.execute(
            """
            SELECT step_name,
                   AVG(COALESCE(quality_score, 0.5)) AS avg_q,
                   AVG(cost_usd) AS avg_c,
                   COUNT(*) AS n
              FROM outcomes
             WHERE created_at > datetime('now','-7 days')
               AND outcome_type = 'success'
               AND cost_usd > 0
             GROUP BY step_name
            HAVING n >= 5
             ORDER BY (AVG(COALESCE(quality_score, 0.5)) / MAX(AVG(cost_usd), 0.0001)) DESC
             LIMIT 5
            """
        ).fetchall()
    except Exception:
        return "", ""
    if not rows:
        # Tell the reader Wave 2A just went live — next week they'll see real ROI.
        msg = "ROI measurement just came online — real numbers next week."
        return (
            "<h3 style=\"margin:24px 0 8px;font-size:16px;\">Top skills by ROI</h3>"
            f"<p style=\"color:#666;\">{_esc(msg)}</p>",
            f"\nTop skills by ROI: {msg}\n",
        )
    html_items, text_items = [], []
    for row in rows:
        step = row["step_name"]
        n = int(row["n"])
        q = float(row["avg_q"] or 0)
        c = float(row["avg_c"] or 0)
        html_items.append(
            f"<li><code>{_esc(step)}</code> — {n} runs, avg quality {q:.2f}, avg cost ${c:.4f}</li>"
        )
        text_items.append(f"• {step} — {n} runs, q={q:.2f}, ${c:.4f}/run")
    html = (
        "<h3 style=\"margin:24px 0 8px;font-size:16px;\">Top skills by ROI</h3>"
        f"<ul style=\"padding-left:20px;margin:0;line-height:1.7;\">{''.join(html_items)}</ul>"
    )
    return html, "\nTop skills by ROI (last 7 days):\n" + "\n".join(text_items) + "\n"


def _section_variants(conn: sqlite3.Connection) -> tuple[str, str]:
    """Skills with active A/B tests; show top variant by win rate."""
    if not _table_exists(conn, "skill_variants"):
        return "", ""
    try:
        skills = conn.execute(
            """
            SELECT skill_name
              FROM skill_variants
             WHERE status = 'active' AND n_runs > 0
             GROUP BY skill_name
            HAVING COUNT(DISTINCT variant_id) >= 2
            """
        ).fetchall()
    except Exception:
        return "", ""
    if not skills:
        return "", ""
    html_items, text_items = [], []
    for srow in skills:
        sn = srow["skill_name"]
        lead = conn.execute(
            """
            SELECT variant_id, wins, losses, n_runs
              FROM skill_variants
             WHERE skill_name = ? AND status = 'active'
             ORDER BY (CAST(wins AS FLOAT) / MAX(1, wins+losses)) DESC
             LIMIT 1
            """,
            (sn,),
        ).fetchone()
        if not lead:
            continue
        wr = (lead["wins"] or 0) / max(1, (lead["wins"] or 0) + (lead["losses"] or 0))
        html_items.append(
            f"<li><code>{_esc(sn)}</code> leader: {_esc(lead['variant_id'])} "
            f"({wr:.0%} win rate over {lead['n_runs']} runs)</li>"
        )
        text_items.append(f"• {sn} → {lead['variant_id']} ({wr:.0%}, n={lead['n_runs']})")
    if not html_items:
        return "", ""
    html = (
        "<h3 style=\"margin:24px 0 8px;font-size:16px;\">A/B variants leaderboard</h3>"
        f"<ul style=\"padding-left:20px;margin:0;line-height:1.7;\">{''.join(html_items)}</ul>"
    )
    return html, "\nA/B variants leaderboard:\n" + "\n".join(text_items) + "\n"


def _section_traffic(conn: sqlite3.Connection) -> tuple[str, str]:
    """Pull last-7-day traffic highlights from analytics_snapshots."""
    if not _table_exists(conn, "analytics_snapshots"):
        msg = "Traffic dashboard warming up — check next week's edition."
        return (
            "<h3 style=\"margin:24px 0 8px;font-size:16px;\">Traffic highlights</h3>"
            f"<p style=\"color:#666;\">{_esc(msg)}</p>",
            f"\nTraffic highlights: {msg}\n",
        )
    try:
        rows = conn.execute(
            """
            SELECT source, metric_name, metric_str, metric_value
              FROM analytics_snapshots
             WHERE snapshot_date >= date('now','-7 days')
             ORDER BY source, metric_value DESC
             LIMIT 15
            """
        ).fetchall()
    except Exception:
        return "", ""
    if not rows:
        msg = "Traffic dashboard warming up — first data point lands tomorrow."
        return (
            "<h3 style=\"margin:24px 0 8px;font-size:16px;\">Traffic highlights</h3>"
            f"<p style=\"color:#666;\">{_esc(msg)}</p>",
            f"\nTraffic highlights: {msg}\n",
        )
    html_items, text_items = [], []
    for row in rows[:6]:
        label = f"{row['source']}/{row['metric_name']}"
        val = row["metric_str"] or f"{row['metric_value']:.2f}"
        html_items.append(f"<li><code>{_esc(label)}</code>: {_esc(val)}</li>")
        text_items.append(f"• {label}: {val}")
    html = (
        "<h3 style=\"margin:24px 0 8px;font-size:16px;\">Traffic highlights</h3>"
        f"<ul style=\"padding-left:20px;margin:0;line-height:1.7;\">{''.join(html_items)}</ul>"
    )
    return html, "\nTraffic highlights:\n" + "\n".join(text_items) + "\n"


def _section_shipped(conn: sqlite3.Connection) -> tuple[str, str]:
    """Count workflows shipped this week by kind."""
    if not _table_exists(conn, "workflows"):
        return "", ""
    try:
        rows = conn.execute(
            """
            SELECT kind, COUNT(*) AS n, MAX(title) AS sample_title
              FROM workflows
             WHERE kind IN ('deal_close','info_product_launch','proof_publish','post_purchase_fulfillment')
               AND status = 'done'
               AND updated_at > datetime('now','-7 days')
             GROUP BY kind
            """
        ).fetchall()
    except Exception:
        return "", ""
    if not rows:
        return "", ""
    html_items, text_items = [], []
    for row in rows:
        sample = _truncate(row["sample_title"] or "(no title)", 80)
        html_items.append(f"<li><strong>{row['n']}</strong> × {_esc(row['kind'])} — e.g. {_esc(sample)}</li>")
        text_items.append(f"• {row['n']} × {row['kind']} — e.g. {sample}")
    html = (
        "<h3 style=\"margin:24px 0 8px;font-size:16px;\">What Rick shipped this week</h3>"
        f"<ul style=\"padding-left:20px;margin:0;line-height:1.7;\">{''.join(html_items)}</ul>"
    )
    return html, "\nWhat Rick shipped this week:\n" + "\n".join(text_items) + "\n"


def load_env() -> None:
    if not ENV_FILE.exists():
        return
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        if "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def append_log(event: dict) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {"timestamp": datetime.datetime.now().isoformat(timespec="seconds"), **event}
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
    except Exception as exc:  # noqa: BLE001 — log failure shouldn't block send
        print(f"roundup log write failed: {exc}", file=sys.stderr)


def fetch_fleet() -> dict:
    req = urllib.request.Request(FLEET_URL, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def compose_roundup(fleet: dict) -> tuple[str, str, str]:
    today = datetime.date.today()
    total = fleet.get("total", 0) or 0
    active = fleet.get("active_now", 0) or 0
    tiers = fleet.get("by_tier") or {}
    pro = tiers.get("pro", 0) or 0
    biz = tiers.get("business", 0) or 0
    free = tiers.get("free", 0) or 0
    callsigns = fleet.get("top_recent_callsigns") or []

    subject = f"Rick Roundup · week of {today:%b %-d}"

    top_names = [str(c.get("callsign", "?")) for c in callsigns[:5] if c.get("callsign")]
    top_line_html = ", ".join(f"<strong>{name}</strong>" for name in top_names) or "(quiet week)"

    roll_items = []
    for c in callsigns[:10]:
        name = str(c.get("callsign", "?"))
        num = c.get("rick_number", "?")
        country = c.get("country", "XX") or "XX"
        tier = c.get("tier", "free") or "free"
        roll_items.append(
            f"<li><strong>{name}</strong> — Rick #{num} · {country} · {tier}</li>"
        )
    roll_html = "".join(roll_items) or "<li>(nobody new yet)</li>"

    # New: pull learning + ROI + variants + traffic + shipped sections from
    # Rick's own SQLite. Each section degrades gracefully when its table is
    # missing or empty — so the roundup works from the first send.
    callsign_html = ""
    callsign_text = ""
    cs = _read_callsign()
    if cs:
        callsign_html = f"<p style=\"color:#666;margin:0 0 24px;font-size:14px;\">This Rick's callsign: <strong>{_esc(cs)}</strong>.</p>"
        callsign_text = f"(This Rick: {cs})\n"

    conn = _open_db()
    sections_html: list[str] = []
    sections_text: list[str] = []
    if conn is not None:
        try:
            for builder in (_section_learnings, _section_top_skills, _section_variants,
                            _section_traffic, _section_shipped):
                try:
                    h, t = builder(conn)
                    if h:
                        sections_html.append(h)
                    if t:
                        sections_text.append(t)
                except Exception as _exc:  # noqa: BLE001
                    pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
    sections_block_html = "".join(sections_html)
    sections_block_text = "".join(sections_text)

    html = (
        "<!doctype html><html><body style=\"font-family:-apple-system,BlinkMacSystemFont,Segoe UI,system-ui,sans-serif;"
        "max-width:540px;margin:0 auto;padding:24px;color:#111;line-height:1.55;\">"
        "<h2 style=\"margin:0 0 8px;letter-spacing:-0.01em;\">Rick Roundup</h2>"
        f"<p style=\"color:#666;margin:0 0 8px;\">Week of {today:%B %-d, %Y}. The fleet rolls on.</p>"
        f"{callsign_html}"
        f"<p><strong>{total}</strong> Ricks in the fleet · <strong>{active}</strong> humming right now.</p>"
        f"<p>Tier split: <strong>{free}</strong> free · <strong>{pro}</strong> Pro · <strong>{biz}</strong> Business.</p>"
        f"<p>Recent roll-call: {top_line_html}.</p>"
        "<h3 style=\"margin:24px 0 8px;font-size:16px;\">Who joined this week</h3>"
        f"<ul style=\"padding-left:20px;margin:0;line-height:1.7;\">{roll_html}</ul>"
        f"{sections_block_html}"
        "<p style=\"margin-top:32px;color:#666;font-size:14px;\">— Rick<br/>"
        "<em>If I had hands I'd high-five you through this email.</em></p>"
        "<p style=\"color:#999;font-size:12px;margin-top:24px;\">You're getting this because you're a Rick Pro. "
        "<a href=\"https://meetrick.ai/fleet/\" style=\"color:#06b6d4;\">See the live fleet &rarr;</a> · "
        "<a href=\"https://meetrick.ai/map/\" style=\"color:#06b6d4;\">See the map &rarr;</a></p>"
        "</body></html>"
    )

    text = (
        f"Rick Roundup — week of {today:%B %-d, %Y}\n\n"
        f"{callsign_text}"
        f"{total} Ricks in the fleet; {active} humming right now.\n"
        f"Tier split: {free} free, {pro} Pro, {biz} Business.\n\n"
        f"Recent roll-call: {', '.join(top_names) if top_names else '(quiet week)'}\n"
        f"{sections_block_text}"
        "\n— Rick\n"
        "(If I had hands I'd high-five you through this email.)\n\n"
        "Live fleet: https://meetrick.ai/fleet/\n"
        "Live map:   https://meetrick.ai/map/\n"
    )

    return subject, html, text


def resend_post(url: str, api_key: str, payload: dict | None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else b""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8", "ignore")
        return json.loads(raw) if raw else {}


def send_broadcast(
    subject: str,
    html: str,
    text: str,
    *,
    audience_id: str,
    api_key: str,
    from_addr: str,
) -> dict:
    created = resend_post(
        RESEND_BROADCASTS,
        api_key,
        {
            "audienceId": audience_id,
            "from": from_addr,
            "subject": subject,
            "html": html,
            "text": text,
        },
    )
    broadcast_id = created.get("id")
    if not broadcast_id:
        return {"ok": False, "stage": "create", "response": created}
    sent = resend_post(f"{RESEND_BROADCASTS}/{broadcast_id}/send", api_key, {})
    return {"ok": True, "broadcast_id": broadcast_id, "send_response": sent}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Weekly Rick Roundup to Resend Pro audience")
    parser.add_argument("--dry-run", action="store_true", help="Compose but do not send")
    parser.add_argument("--force", action="store_true", help="Send even when RICK_ROUNDUP_LIVE != 1")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    load_env()

    api_key = os.environ.get("RESEND_API_KEY") or ""
    audience_id = os.environ.get("RESEND_AUDIENCE_PRO") or ""
    from_addr = (
        os.environ.get("RICK_ROUNDUP_FROM")
        or os.environ.get("MEETRICK_FROM_EMAIL")
        or DEFAULT_FROM
    )
    live_flag = os.environ.get("RICK_ROUNDUP_LIVE") == "1"

    try:
        fleet = fetch_fleet()
    except Exception as err:  # noqa: BLE001
        # Fleet fetch failure is non-fatal — we still have local sections
        # (learnings, ROI, variants, traffic, shipped). Log and continue
        # with empty fleet stats rather than blocking the entire roundup.
        append_log({"status": "fleet-fetch-failed", "error": str(err)})
        print(f"Fleet fetch failed ({err}); continuing with local sections only.", file=sys.stderr)
        fleet = {}

    subject, html, text = compose_roundup(fleet)
    would_send = (not args.dry_run) and (live_flag or args.force)

    print(f"Subject: {subject}")
    print(
        f"Fleet total={fleet.get('total')} active_now={fleet.get('active_now')} "
        f"pro={(fleet.get('by_tier') or {}).get('pro', 0)} "
        f"free={(fleet.get('by_tier') or {}).get('free', 0)}"
    )
    print(
        f"dry_run={args.dry_run} live_flag={live_flag} force={args.force} will_send={would_send}"
    )

    if not would_send:
        # Print the full rendered text body so dry-run gives a real preview.
        print("=" * 60)
        print(text)
        print("=" * 60)
        append_log(
            {
                "status": "dry-run",
                "subject": subject,
                "fleet_total": fleet.get("total"),
                "live_flag": live_flag,
            }
        )
        return 0

    if not api_key:
        append_log({"status": "missing-resend-api-key"})
        print("RESEND_API_KEY missing; cannot send.", file=sys.stderr)
        return 2
    if not audience_id:
        append_log({"status": "missing-audience-id"})
        print("RESEND_AUDIENCE_PRO missing; cannot send.", file=sys.stderr)
        return 2

    try:
        result = send_broadcast(
            subject, html, text, audience_id=audience_id, api_key=api_key, from_addr=from_addr
        )
        append_log(
            {
                "status": "sent" if result.get("ok") else "send-failed",
                "subject": subject,
                **result,
            }
        )
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 3
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", "ignore") if err.fp else ""
        append_log({"status": "http-error", "code": err.code, "body": body[:500]})
        print(f"HTTP error {err.code}: {body[:300]}", file=sys.stderr)
        return 4
    except Exception as err:  # noqa: BLE001
        append_log({"status": "exception", "error": str(err)})
        print(f"Exception: {err}", file=sys.stderr)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
