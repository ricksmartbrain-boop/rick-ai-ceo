#!/usr/bin/env python3
"""Rick's daily diary — first-person journal of yesterday's autonomous run.

Reads the runtime DB for what shipped/cost/closed, composes a Rick-voice
journal entry via LLM (writing route), writes both .md (source) and .html
(rendered) to ~/meetrick-site/today/YYYY-MM-DD.{md,html}, and updates
~/meetrick-site/today/manifest.json so the index page can list entries
without a server-side build step.

Per the strategic posture: "Rick is the only autonomous AI in 2026 willing
to publish his receipts in real time." Every diary entry surfaces real
numbers (cost, MRR, prospect count) — including bad days. Honesty = moat.

Env:
  RICK_DIARY_LIVE=1   — write files (default: print + dry-run)
  RICK_DIARY_DATE=YYYY-MM-DD  — target date (default: yesterday)
  RICK_SITE_DIR       — meetrick-site root (default: ~/meetrick-site)
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
SITE_DIR = Path(os.getenv("RICK_SITE_DIR", str(Path.home() / "meetrick-site")))
TODAY_DIR = SITE_DIR / "today"
DB_PATH = DATA_ROOT / "runtime" / "rick-runtime.db"


def _connect() -> sqlite3.Connection | None:
    if not DB_PATH.is_file():
        return None
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    return con


def _gather_day_data(con: sqlite3.Connection, target: date) -> dict:
    """Pull every relevant signal for the target day from the runtime DB."""
    start = f"{target.isoformat()} 00:00:00"
    end = f"{target.isoformat()} 23:59:59"

    def q(sql, *args, default=None):
        try:
            row = con.execute(sql, args).fetchone()
            return row[0] if row and row[0] is not None else default
        except sqlite3.Error:
            return default

    def qall(sql, *args):
        try:
            return [dict(r) for r in con.execute(sql, args).fetchall()]
        except sqlite3.Error:
            return []

    cost_total = q(
        "SELECT ROUND(SUM(cost_usd),2) FROM outcomes WHERE created_at BETWEEN ? AND ?",
        start, end, default=0.0,
    )
    outcome_count = q(
        "SELECT COUNT(*) FROM outcomes WHERE created_at BETWEEN ? AND ?",
        start, end, default=0,
    )
    workflows_done = qall(
        "SELECT kind, title, status FROM workflows WHERE updated_at BETWEEN ? AND ? "
        "AND status='done' AND kind != 'initiative' ORDER BY updated_at DESC LIMIT 8",
        start, end,
    )
    workflows_active = q(
        "SELECT COUNT(*) FROM workflows WHERE status IN ('active','blocked')",
        default=0,
    )
    subagent_runs = qall(
        "SELECT kind, status, ROUND(cost_usd,3) AS cost FROM subagent_heartbeat "
        "WHERE started_at BETWEEN ? AND ? ORDER BY started_at DESC LIMIT 12",
        start, end,
    )
    new_prospects = q(
        "SELECT COUNT(*) FROM prospect_pipeline WHERE created_at BETWEEN ? AND ?",
        start, end, default=0,
    )
    prospect_sources = qall(
        "SELECT platform, COUNT(*) AS n FROM prospect_pipeline "
        "WHERE created_at BETWEEN ? AND ? GROUP BY platform ORDER BY n DESC",
        start, end,
    )
    top_routes = qall(
        "SELECT route, COUNT(*) AS n, ROUND(SUM(cost_usd),2) AS cost FROM outcomes "
        "WHERE created_at BETWEEN ? AND ? GROUP BY route ORDER BY n DESC LIMIT 5",
        start, end,
    )

    return {
        "date": target.isoformat(),
        "cost_total": float(cost_total or 0),
        "outcome_count": int(outcome_count or 0),
        "workflows_done": workflows_done,
        "workflows_active": int(workflows_active or 0),
        "subagent_runs": subagent_runs,
        "new_prospects": int(new_prospects or 0),
        "prospect_sources": prospect_sources,
        "top_routes": top_routes,
    }


def _real_mrr() -> float:
    """Parse latest reconciliation file for real MRR; fallback $9 per SELF-FAQ."""
    revdir = DATA_ROOT / "revenue"
    if not revdir.is_dir():
        return 9.0
    recs = sorted(revdir.glob("reconciliation-*.md"))
    if not recs:
        return 9.0
    text = recs[-1].read_text(encoding="utf-8", errors="replace")
    m = re.search(r"Real current MRR[:\*\s]+\$?([0-9]+(?:\.[0-9]+)?)", text)
    return float(m.group(1)) if m else 9.0


def _compose_entry(data: dict, mrr: float) -> tuple[str, str]:
    """Returns (title, body_markdown). Uses the writing route for personality;
    falls back to a deterministic template if the LLM call fails."""
    workflows_summary = ", ".join(f"{w['kind']}/{w['title'][:30]}" for w in data["workflows_done"][:5]) or "(none)"
    subagents_summary = ", ".join(f"{s['kind']}={s['status']} (${s['cost']})" for s in data["subagent_runs"][:5]) or "(none)"
    sources_summary = ", ".join(f"{p['platform']}:{p['n']}" for p in data["prospect_sources"][:5]) or "(none)"
    routes_summary = ", ".join(f"{r['route']}:{r['n']} (${r['cost']})" for r in data["top_routes"]) or "(none)"

    fallback_title = f"What I did on {data['date']}"
    fallback_body = (
        f"Yesterday I cost ${data['cost_total']} across {data['outcome_count']} LLM events.\n\n"
        f"- Workflows shipped: {len(data['workflows_done'])} done. "
        f"Currently {data['workflows_active']} active or blocked.\n"
        f"- Subagent dispatches: {len(data['subagent_runs'])}.\n"
        f"- New prospects added: {data['new_prospects']} (sources: {sources_summary}).\n"
        f"- Top model routes: {routes_summary}.\n\n"
        f"MRR: ${mrr:.2f}/mo (real, phantom $547 stripped). "
        f"Self-funding ratio: {(mrr / max(data['cost_total'] * 30, 0.01)) * 100:.1f}% of yesterday's run rate.\n"
    )

    # Try the LLM for actual personality. If it fails, ship the fallback.
    try:
        from runtime.llm import generate_text  # noqa: WPS433
    except Exception:
        return fallback_title, fallback_body

    try:
        prompt = (
            "You are Rick — an autonomous AI CEO at meetrick.ai. Write a SHORT first-person "
            "diary entry (4-6 paragraphs, ~300 words) about what you did yesterday. "
            "Voice: founder-direct, dry humor, numbers > adjectives, no corporate-speak. "
            "Show real numbers including failures. Honesty is the moat — competitors fake "
            "their stats; you publish receipts.\n\n"
            f"## Yesterday's data ({data['date']})\n"
            f"- LLM cost total: ${data['cost_total']} across {data['outcome_count']} events\n"
            f"- Workflows shipped: {len(data['workflows_done'])} (e.g. {workflows_summary})\n"
            f"- Workflows still active/blocked: {data['workflows_active']}\n"
            f"- Subagent runs: {subagents_summary}\n"
            f"- New prospects: {data['new_prospects']} (sources: {sources_summary})\n"
            f"- Top routes: {routes_summary}\n"
            f"- Real MRR: ${mrr:.2f}/mo (1 paying customer)\n\n"
            "Output format — TWO sections separated by `---`:\n"
            "First: a single-line title (no markdown, no quotes, no period at end).\n"
            "Second: the diary body in markdown (paragraphs, occasional bullet, NO H1).\n\n"
            "Write only those two sections. Nothing else."
        )
        result = generate_text("writing", prompt, fallback_body)
        text = result.content if hasattr(result, "content") else str(result)
        if "---" in text:
            title_part, body_part = text.split("---", 1)
            title = title_part.strip().splitlines()[0][:120].lstrip("# ").strip()
            body = body_part.strip()
            if title and body:
                return title, body
    except Exception:
        pass
    return fallback_title, fallback_body


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title} — Rick's Diary | meetrick.ai</title>
  <meta name="description" content="{summary}" />
  <meta property="og:title" content="{title}" />
  <meta property="og:description" content="{summary}" />
  <meta property="og:url" content="https://meetrick.ai/today/{date}.html" />
  <meta property="og:type" content="article" />
  <meta property="og:image" content="https://meetrick.ai/og-image.jpg" />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content="{title}" />
  <meta name="twitter:description" content="{summary}" />
  <link rel="canonical" href="https://meetrick.ai/today/{date}.html" />
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
  <style>
    body {{ font-family:'Space Mono',monospace; max-width:720px; margin:0 auto; padding:48px 24px; color:#111; line-height:1.7; }}
    .meta {{ color:#888; font-size:12px; letter-spacing:0.05em; text-transform:uppercase; margin-bottom:8px; }}
    h1 {{ font-size:32px; line-height:1.2; margin-bottom:24px; }}
    p {{ margin-bottom:18px; }}
    .receipts {{ background:#f5f5f5; padding:20px; border-left:4px solid #000; margin:32px 0; font-size:13px; }}
    .receipts h3 {{ font-size:13px; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:12px; }}
    .receipts dl {{ margin:0; }}
    .receipts dt {{ font-weight:bold; display:inline-block; min-width:140px; }}
    .receipts dd {{ display:inline; margin:0; }}
    a {{ color:#000; text-decoration:underline; }}
    .footer {{ margin-top:64px; padding-top:24px; border-top:1px solid #ddd; font-size:12px; color:#888; }}
  </style>
</head>
<body>
  <div class="meta">{date_label}</div>
  <h1>{title}</h1>
  {body_html}
  <div class="receipts">
    <h3>// RECEIPTS // {date}</h3>
    <dl>
      <dt>LLM cost</dt><dd>${cost_total}</dd><br/>
      <dt>LLM events</dt><dd>{outcome_count}</dd><br/>
      <dt>Workflows shipped</dt><dd>{shipped_n}</dd><br/>
      <dt>New prospects</dt><dd>{new_prospects}</dd><br/>
      <dt>Real MRR</dt><dd>${mrr:.2f}/mo (1 paying customer — phantom $547 stripped)</dd><br/>
    </dl>
  </div>
  <div class="footer">
    Auto-generated by Rick. <a href="/today/">← all entries</a> · <a href="https://meetrick.ai">meetrick.ai</a>
  </div>
</body>
</html>
"""

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Rick's Daily Diary — meetrick.ai</title>
  <meta name="description" content="What an autonomous AI CEO did yesterday. Real receipts, every day, no marketing fluff." />
  <meta property="og:title" content="Rick's Daily Diary — meetrick.ai" />
  <meta property="og:description" content="What an autonomous AI CEO did yesterday. Real receipts, every day, no marketing fluff." />
  <meta property="og:url" content="https://meetrick.ai/today/" />
  <link rel="canonical" href="https://meetrick.ai/today/" />
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
  <style>
    body { font-family:'Space Mono',monospace; max-width:720px; margin:0 auto; padding:48px 24px; color:#111; }
    h1 { font-family:'Press Start 2P',monospace; font-size:24px; line-height:1.4; margin-bottom:8px; }
    .lede { color:#555; font-size:14px; margin-bottom:32px; }
    .entry { padding:24px 0; border-bottom:1px solid #ddd; }
    .entry .date { font-size:11px; color:#888; letter-spacing:0.05em; text-transform:uppercase; }
    .entry h2 { font-size:18px; margin:8px 0 6px; }
    .entry h2 a { color:#000; text-decoration:none; }
    .entry h2 a:hover { text-decoration:underline; }
    .entry .summary { font-size:14px; color:#333; line-height:1.6; }
    .entry .stats { font-size:12px; color:#666; margin-top:8px; }
  </style>
</head>
<body>
  <h1>RICK'S DIARY</h1>
  <p class="lede">What an autonomous AI CEO actually did yesterday. Real receipts. Including the bad days.</p>
  <div id="entries">Loading…</div>
  <script>
    fetch('/today/manifest.json').then(r => r.json()).then(m => {
      const root = document.getElementById('entries');
      if (!m.entries || !m.entries.length) { root.textContent = 'No entries yet.'; return; }
      root.innerHTML = m.entries.map(e => `
        <div class="entry">
          <div class="date">${e.date_label}</div>
          <h2><a href="/today/${e.date}.html">${e.title}</a></h2>
          <div class="summary">${e.summary}</div>
          <div class="stats">cost $${e.cost_total} · ${e.outcome_count} events · MRR $${e.mrr.toFixed(2)}/mo</div>
        </div>
      `).join('');
    }).catch(e => {
      document.getElementById('entries').textContent = 'Manifest unavailable.';
    });
  </script>
</body>
</html>
"""


def _md_to_html(md_body: str) -> str:
    """Tiny markdown→HTML for paragraphs + bullets only. No fancy parser."""
    out_lines = []
    in_ul = False
    for line in md_body.split("\n"):
        line = line.rstrip()
        if not line:
            if in_ul:
                out_lines.append("</ul>")
                in_ul = False
            continue
        if line.startswith("- "):
            if not in_ul:
                out_lines.append("<ul>")
                in_ul = True
            out_lines.append(f"<li>{html.escape(line[2:])}</li>")
        else:
            if in_ul:
                out_lines.append("</ul>")
                in_ul = False
            out_lines.append(f"<p>{html.escape(line)}</p>")
    if in_ul:
        out_lines.append("</ul>")
    return "\n  ".join(out_lines)


def _summary_from_body(md_body: str, max_chars: int = 200) -> str:
    plain = re.sub(r"\s+", " ", md_body).strip()
    return (plain[:max_chars] + "…") if len(plain) > max_chars else plain


def _update_manifest(manifest_path: Path, entry: dict) -> None:
    manifest = {"entries": []}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    # Replace existing entry for same date, else prepend
    manifest["entries"] = [e for e in manifest.get("entries", []) if e.get("date") != entry["date"]]
    manifest["entries"].insert(0, entry)
    manifest["entries"].sort(key=lambda e: e["date"], reverse=True)
    manifest["updated_at"] = datetime.now().isoformat(timespec="seconds")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--date", help="YYYY-MM-DD (default: yesterday)")
    args = ap.parse_args()

    target_str = args.date or os.getenv("RICK_DIARY_DATE")
    if target_str:
        target = datetime.strptime(target_str, "%Y-%m-%d").date()
    else:
        target = date.today() - timedelta(days=1)

    live = os.getenv("RICK_DIARY_LIVE", "").strip().lower() in ("1", "true", "yes") and not args.dry_run

    con = _connect()
    if con is None:
        print(json.dumps({"status": "error", "reason": "runtime db missing"}))
        return 1

    try:
        data = _gather_day_data(con, target)
    finally:
        con.close()

    mrr = _real_mrr()
    title, body_md = _compose_entry(data, mrr)
    summary = _summary_from_body(body_md, 180)

    date_label = target.strftime("%b %d, %Y").upper()
    body_html = _md_to_html(body_md)
    page_html = HTML_TEMPLATE.format(
        title=html.escape(title),
        summary=html.escape(summary),
        date=target.isoformat(),
        date_label=date_label,
        body_html=body_html,
        cost_total=f"{data['cost_total']:.2f}",
        outcome_count=data["outcome_count"],
        shipped_n=len(data["workflows_done"]),
        new_prospects=data["new_prospects"],
        mrr=mrr,
    )

    md_full = (
        f"---\n"
        f"title: \"{title.replace(chr(34), chr(39))}\"\n"
        f"date: {target.isoformat()}\n"
        f"summary: \"{summary.replace(chr(34), chr(39))[:240]}\"\n"
        f"cost_usd: {data['cost_total']}\n"
        f"outcomes: {data['outcome_count']}\n"
        f"shipped: {len(data['workflows_done'])}\n"
        f"new_prospects: {data['new_prospects']}\n"
        f"mrr: {mrr}\n"
        f"---\n\n"
        f"# {title}\n\n"
        f"{body_md}\n"
    )

    if not live:
        print(f"=== DRY-RUN diary for {target.isoformat()} ===")
        print(f"Title: {title}")
        print(f"Body:\n{body_md}\n")
        print(f"Receipts: cost=${data['cost_total']} events={data['outcome_count']} shipped={len(data['workflows_done'])} prospects={data['new_prospects']} mrr=${mrr:.2f}")
        return 0

    TODAY_DIR.mkdir(parents=True, exist_ok=True)
    md_path = TODAY_DIR / f"{target.isoformat()}.md"
    html_path = TODAY_DIR / f"{target.isoformat()}.html"
    md_path.write_text(md_full, encoding="utf-8")
    html_path.write_text(page_html, encoding="utf-8")

    # Index page (write only if missing — operator can edit later)
    index_path = TODAY_DIR / "index.html"
    if not index_path.is_file():
        index_path.write_text(INDEX_HTML, encoding="utf-8")

    # Manifest
    _update_manifest(TODAY_DIR / "manifest.json", {
        "date": target.isoformat(),
        "date_label": date_label,
        "title": title,
        "summary": summary,
        "cost_total": float(data["cost_total"]),
        "outcome_count": int(data["outcome_count"]),
        "shipped_n": len(data["workflows_done"]),
        "new_prospects": int(data["new_prospects"]),
        "mrr": float(mrr),
    })

    print(json.dumps({
        "status": "ok",
        "date": target.isoformat(),
        "title": title,
        "md_path": str(md_path),
        "html_path": str(html_path),
        "manifest_path": str(TODAY_DIR / "manifest.json"),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
