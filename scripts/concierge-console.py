#!/usr/bin/env python3
"""
concierge-console.py — one-shot generator for the concierge hand-send console.

Reads ~/rick-vault/go-to-market/concierge-batch-2026-07-14/ (CHECKLIST.md +
the NN-*.md drafts) and writes send-console.html into that same directory:
a single self-contained local HTML page (file://, no server, no external
resources) that collapses each hand-send to a few clicks — mailto links with
the subject+body taken VERBATIM from the draft, copy-to-clipboard buttons,
and per-row helper commands (suppression grep, CHECKLIST tick sed).

The page is a keyboard, NOT a ledger: sent/unsent state is read from
CHECKLIST.md ticks at generation time, and CHECKLIST.md stays the ONLY
machine-readable record (day14-gate.py counts those ticks every heartbeat).
The page performs no writes and transmits nothing; the address inputs
persist to localStorage only.

The generated HTML contains real prospect emails — it stays LOCAL, never
published, never shared.

Deterministic parsing, zero rewriting of draft content, stdlib only, no LLM.
Fails loud (nonzero exit) if any draft is missing or fails to parse.

D3/D2 additions (2026-07-19 spec): renders the NN-dossier.md files and
hn-cards.json that scripts/concierge-dossier.py generates — ALL LLM work is
isolated in that script; this one only renders what is on disk. Per-card
collapsible dossier block ('SITE DIFFERS' dossiers open by default), cards
ordered by expected value (warmest signal / most verified facts first; HN
cards freshest thread first), CONCIERGE_BOOKING_URL rendered only when the
env var is set (cal.com drops in later, zero code change).

Usage: python3 scripts/concierge-console.py
Re-run after any draft, dossier, or CHECKLIST edit — the page is a snapshot.
"""
import datetime
import html
import json
import os
import re
import sys
import urllib.parse

BATCH_DIR = os.path.expanduser(
    "~/rick-vault/go-to-market/concierge-batch-2026-07-14")
OUT_HTML = os.path.join(BATCH_DIR, "send-console.html")
# 21 = the original 20 plus 21-nada-tunelab.md (2026-07-19 replacement for
# draft 20, which is DO-NOT-SEND — synthetic test persona, kept for audit).
EXPECTED_DRAFTS = 21
# Practical mailto URL limit: above this many URL-encoded body chars the
# mailto carries subject only and the row directs to the copy button.
MAILTO_BODY_LIMIT = 1800
# D2 Phase-1 cards written by scripts/concierge-dossier.py (never by hand).
HN_CARDS_JSON = os.path.join(BATCH_DIR, "hn-cards.json")
# cal.com lands later with ZERO code change: link renders only when set.
BOOKING_URL = os.environ.get("CONCIERGE_BOOKING_URL", "").strip()
# Marker string the dossier generator emits on re-fetch drift (Rule 7).
DIFFERS_MARKER = "SITE DIFFERS FROM DRAFT ASSUMPTIONS"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Bare domains / URLs in the To/Channel lines (not preceded by @ = handles,
# not part of an email address).
URL_RE = re.compile(
    r"(?<![@\w])((?:https?://)?(?:[\w-]+\.)+[a-z]{2,}(?:/[^\s`,)]*)?)")


def parse_draft(text, fname):
    """Extract title / To / Channel / Subject / Body verbatim from one draft.

    Body is everything between the '**Body:**' line and the '## Related'
    header, with only leading/trailing blank lines stripped — no rewriting.
    Raises ValueError (fail loud) if any required piece is missing.
    """
    title = to_line = channel_line = subject = None
    lines = text.split("\n")
    body_start = body_end = None
    for i, ln in enumerate(lines):
        if title is None and ln.startswith("# "):
            title = ln[2:].strip()
        elif ln.startswith("- To: "):
            to_line = ln[len("- To: "):].strip()
        elif ln.startswith("- Channel: "):
            channel_line = ln[len("- Channel: "):].strip()
        elif ln.startswith("**Subject:**"):
            subject = ln[len("**Subject:**"):].strip()
        elif ln.strip() == "**Body:**" and body_start is None:
            body_start = i + 1
        elif ln.startswith("## Related") and body_start is not None:
            body_end = i
            break
    missing = [k for k, v in [("title", title), ("To", to_line),
                              ("Channel", channel_line), ("Subject", subject),
                              ("Body start", body_start), ("Body end", body_end)]
               if v is None]
    if missing:
        raise ValueError("%s: missing %s" % (fname, ", ".join(missing)))
    body = "\n".join(lines[body_start:body_end]).strip("\n")
    return {"title": title, "to": to_line, "channel": channel_line,
            "subject": subject, "body": body}


def channel_badge(channel_line):
    """Short badge from the verbatim channel line (display only)."""
    low = channel_line.lower()
    has_email = "email" in low or "mailbox" in low
    has_dm = "dm" in low
    if has_email and has_dm:
        return "email/DM"
    if has_dm:
        return "DM"
    if has_email:
        return "email"
    return "other"


def linkify(text_escaped):
    """Turn bare domains/URLs in an HTML-escaped string into <a> links."""
    def repl(m):
        raw = m.group(1)
        href = raw if raw.startswith("http") else "https://" + raw
        return '<a href="%s" target="_blank">%s</a>' % (href, raw)
    return URL_RE.sub(repl, text_escaped)


def read_ticks(checklist_text):
    """filename -> {ticked, do_not_send, note} from CHECKLIST.md right now.

    A tick line may carry a trailing annotation after the filename (the #20
    DO-NOT-SEND note added 2026-07-19) — day14-gate.py's parser tolerates
    that, so this one must too. A 'DO-NOT-SEND' marker in the annotation
    flags the draft as blocked: rendered as a dead row, no mailto, no
    buttons.
    """
    ticks = {}
    for m in re.finditer(r"^- \[( |x|X)\] (\S+\.md)(?:[ \t]+(.*))?$",
                         checklist_text, re.MULTILINE):
        note = (m.group(3) or "").strip()
        ticks[m.group(2)] = {"ticked": m.group(1).lower() == "x",
                             "do_not_send": "DO-NOT-SEND" in note,
                             "note": note}
    return ticks


def extract_section(checklist_text, header_prefix):
    """Verbatim section body from CHECKLIST.md (header line to next '## ')."""
    lines = checklist_text.split("\n")
    start = None
    for i, ln in enumerate(lines):
        if start is None and ln.startswith(header_prefix):
            start = i + 1
        elif start is not None and ln.startswith("## "):
            return "\n".join(lines[start:i]).strip("\n")
    return "\n".join(lines[start:]).strip("\n") if start is not None else ""


def load_dossiers():
    """num -> {text, facts, differs} from the NN-dossier.md files that
    scripts/concierge-dossier.py wrote. Facts count = verified-quote lines
    (deterministic marker), used only for expected-value ordering."""
    out = {}
    for fname in sorted(os.listdir(BATCH_DIR)):
        m = re.match(r"^(\d{2})-dossier\.md$", fname)
        if not m:
            continue
        with open(os.path.join(BATCH_DIR, fname), encoding="utf-8") as f:
            text = f.read()
        out[m.group(1)] = {"text": text,
                           "facts": text.count("> SOURCE QUOTE:"),
                           "differs": DIFFERS_MARKER in text}
    return out


def build_rows(drafts, ticks, dossiers):
    """Per-draft render/JS data. Order: prefilled first, then 01-10, 11-19,
    with DO-NOT-SEND rows split out (rendered dead, never pinned)."""
    rows = []
    for fname in sorted(drafts):
        d = drafts[fname]
        num = fname[:2]
        tick = ticks.get(fname, {"ticked": False, "do_not_send": False,
                                 "note": ""})
        # The one send-ready draft (21, tunelabid@gmail.com — PH-published)
        # has its address in the To line; everyone else needs Vlad's ~30-sec
        # handle grab. Draft 20 also has an address on file but is
        # DO-NOT-SEND (synthetic test persona, 2026-07-19).
        email_m = EMAIL_RE.search(d["to"])
        prefilled = email_m.group(0) if email_m else ""
        body_crlf = d["body"].replace("\n", "\r\n")
        enc_subject = urllib.parse.quote(d["subject"], safe="")
        enc_body = urllib.parse.quote(body_crlf, safe="")
        too_long = len(enc_body) > MAILTO_BODY_LIMIT
        # Query part of the mailto href; JS prepends 'mailto:' + address.
        query = "?subject=" + enc_subject + (
            "" if too_long else "&body=" + enc_body)
        rows.append({
            "num": num, "file": fname, "title": d["title"], "to": d["to"],
            "channel": d["channel"], "badge": channel_badge(d["channel"]),
            "subject": d["subject"], "body": d["body"], "query": query,
            "prefilled": prefilled, "mailto_too_long": too_long,
            "ticked": tick["ticked"], "do_not_send": tick["do_not_send"],
            "dns_note": tick["note"], "dossier": dossiers.get(num),
        })
    # Pinned: prefilled-address rows — zero-friction. NEVER a DO-NOT-SEND
    # row: those render dead (no mailto, no buttons, no JS data).
    dns = [r for r in rows if r["do_not_send"]]
    pinned = [r for r in rows if r["prefilled"] and not r["do_not_send"]]
    rest = [r for r in rows if not r["prefilled"] and not r["do_not_send"]]
    return pinned, rest, dns


def render_row(r):
    e = html.escape
    num = r["num"]
    classes = "row" + (" sent" if r["ticked"] else "")
    state = ('<span class="state sent-badge">SENT — ticked in CHECKLIST.md</span>'
             if r["ticked"] else
             '<span class="state unsent-badge">unsent</span>')
    if r["dossier"] and r["dossier"]["differs"]:
        # Drift is surfaced on the collapsed card, never buried (Rule 7).
        state += ('<span class="state differs-badge">⚠️ SITE DIFFERS — '
                  'read dossier before sending</span>')
    if r["dossier"]:
        dossier_html = (
            '<details class="dossier"%s><summary>reply-moment dossier '
            '(%d verified site fact%s)%s</summary><pre class="full">%s</pre>'
            '</details>'
            % (" open" if r["dossier"]["differs"] else "",
               r["dossier"]["facts"],
               "" if r["dossier"]["facts"] == 1 else "s",
               " — ⚠️ SITE DIFFERS" if r["dossier"]["differs"] else "",
               e(r["dossier"]["text"])))
    else:
        dossier_html = ('<div class="note">No dossier on disk yet — run '
                        '<code>python3 scripts/concierge-dossier.py</code> '
                        'then regenerate this page.</div>')
    booking_btn = ('<button onclick="copyText(B,this)">Copy booking link'
                   '</button>' if BOOKING_URL else "")
    preview = "\n".join(
        [ln for ln in r["body"].split("\n") if ln.strip()][:2])
    addr_bits = ""
    if r["prefilled"]:
        addr_bits = ('<span class="prefilled">To: <b>%s</b> (on file — '
                     'send-ready)</span>' % e(r["prefilled"]))
    else:
        addr_bits = ('<input type="text" id="addr-%s" class="addr" '
                     'placeholder="paste grabbed address / handle" '
                     'oninput="saveAddr(\'%s\')">' % (num, num))
    mailto_note = ""
    if r["mailto_too_long"]:
        mailto_note = ('<div class="note warn">Body too long for a mailto '
                       'link — the Open-email button carries the subject '
                       'only; use [Copy body] and paste.</div>')
    dm_btn = ""
    if "DM" in r["badge"]:
        dm_btn = ('<button onclick="copyText(D[\'%s\'].body,this)">'
                  'Copy DM body (no subject — CHECKLIST rule 5)</button>'
                  % num)
    return """
<div class="%s" id="row-%s">
  <div class="rowhead">
    <label class="local"><input type="checkbox" id="done-%s"
      onchange="saveDone('%s')" %s> <span class="localnote">local note only —
      the CHECKLIST.md tick is the ledger</span></label>
    <b>#%s</b> %s
    <span class="badge">%s</span> %s
  </div>
  <div class="meta">To: %s</div>
  <div class="meta">Channel: %s</div>
  <div class="meta">%s</div>
  <div class="meta">Subject: <b>%s</b></div>
  <pre class="preview">%s</pre>
  <details><summary>full body</summary><pre class="full">%s</pre></details>
  %s
  %s
  <div class="btns">
    <button class="primary" onclick="openMail('%s')">Open email (mailto)</button>
    <button onclick="copyText(D['%s'].subject,this)">Copy subject</button>
    <button onclick="copyText(D['%s'].body,this)">Copy body</button>
    %s
    %s
    <button onclick="copySuppression('%s',this)">Copy suppression check</button>
    <button onclick="copyText(D['%s'].tick,this)">Copy tick command</button>
  </div>
  <div class="note">After sending: run the tick command (or hand-edit
  CHECKLIST.md) to flip <code>%s</code> to <code>[x]</code> — that tick is
  the ONLY record the Day-14 gate counts.</div>
</div>""" % (
        classes, num, num, num, "disabled checked" if r["ticked"] else "",
        num, e(r["title"]), e(r["badge"]), state,
        linkify(e(r["to"])), linkify(e(r["channel"])), addr_bits,
        e(r["subject"]), e(preview), e(r["body"]), dossier_html, mailto_note,
        num, num, num, dm_btn, booking_btn, num, num, e(r["file"]))


def render_dns_row(r):
    """DO-NOT-SEND row: unmissable synthetic state, mailto killed, no copy /
    tick / suppression buttons, no js_data entry — nothing on this row can
    help a send happen."""
    e = html.escape
    return """
<div class="row dns" id="row-%s">
  <div class="rowhead">
    <b>#%s</b> %s
    <span class="state dns-badge">⛔ SYNTHETIC — DO NOT SEND</span>
  </div>
  <div class="meta">To: %s</div>
  <div class="meta dnsreason">%s</div>
  <div class="note">Mailto and all buttons removed on purpose. This row
  exists only so nobody re-promotes the draft; its CHECKLIST.md box stays
  unticked forever.</div>
</div>""" % (r["num"], r["num"], e(r["title"]), e(r["to"]),
             e(r["dns_note"]))


def render_hn_card(c, i):
    """D2 Phase-1 card: value-only HN feedback comment, hand-pasted from
    Vlad's PERSONAL account. Zero links / zero product mention were enforced
    mechanically at generation time (concierge-dossier.py) — this renderer
    adds nothing to the text."""
    e = html.escape
    stats = []
    if c.get("age_days") is not None:
        stats.append("thread age %.1fd%s" % (
            c["age_days"],
            "" if c.get("age_source") == "hn_api" else " (from sourcing date)"))
    if c.get("hn_points") is not None:
        stats.append("%s points" % c["hn_points"])
    if c.get("hn_comments") is not None:
        stats.append("%s comments" % c["hn_comments"])
    return """
<div class="row hn" id="hn-%d">
  <div class="rowhead"><b>HN-%d</b> %s
    <span class="badge">HN reply</span>
    <span class="state">%s</span></div>
  <div class="meta">Thread: <a href="%s" target="_blank">%s</a> ·
    product: %s</div>
  <pre class="preview">%s</pre>
  <div class="btns">
    <button class="primary" onclick="copyText(H['%d'].text,this)">Copy
    comment</button>
    <button onclick="copyText(H['%d'].tick,this)">Copy tick-append
    command</button>
  </div>
  <div class="note">Paste from your PERSONAL HN account, on-thread. After
  posting, run the tick-append command — the appended <code>[x]</code> line
  in CHECKLIST.md is the only record (same ledger as the emails).</div>
</div>""" % (c["story_id"], i, e(c.get("story_title", "")),
             e(" · ".join(stats)),
             e(c["hn_thread_url"]), e(c["hn_thread_url"]), e(c["domain"]),
             e(c["comment_text"]), c["story_id"], c["story_id"])


def main():
    if not os.path.isdir(BATCH_DIR):
        sys.exit("FATAL: batch dir missing: %s" % BATCH_DIR)
    checklist_path = os.path.join(BATCH_DIR, "CHECKLIST.md")
    with open(checklist_path, encoding="utf-8") as f:
        checklist_text = f.read()
    ticks = read_ticks(checklist_text)

    drafts = {}
    errors = []
    for fname in sorted(os.listdir(BATCH_DIR)):
        if not re.match(r"^\d{2}-.*\.md$", fname):
            continue
        if fname.endswith("-dossier.md"):
            continue  # dossier files are NOT drafts (concierge-dossier.py)
        with open(os.path.join(BATCH_DIR, fname), encoding="utf-8") as f:
            try:
                drafts[fname] = parse_draft(f.read(), fname)
            except ValueError as exc:
                errors.append(str(exc))
    if errors:
        sys.exit("FATAL: draft parse failures:\n  " + "\n  ".join(errors))
    if len(drafts) != EXPECTED_DRAFTS:
        sys.exit("FATAL: expected %d drafts, found %d: %s"
                 % (EXPECTED_DRAFTS, len(drafts), ", ".join(sorted(drafts))))
    for fname in drafts:
        if fname not in ticks:
            sys.exit("FATAL: %s has no tick line in CHECKLIST.md" % fname)

    dossiers = load_dossiers()
    pinned, rest, dns = build_rows(drafts, ticks, dossiers)
    day1 = [r for r in rest if r["num"] <= "10"]
    day2 = [r for r in rest if r["num"] > "10"]
    # Expected-value ordering (attacks activation energy): within each day
    # block, warmest signal first = most verified dossier facts (least work
    # at the send moment), then draft number. Pinned rows are already the
    # warmest (address on file).
    ev = lambda r: (-(r["dossier"]["facts"] if r["dossier"] else 0), r["num"])
    day1.sort(key=ev)
    day2.sort(key=ev)

    # D2 Phase-1 HN cards (freshest thread first — see-rate decays weekly).
    hn_cards = []
    if os.path.exists(HN_CARDS_JSON):
        with open(HN_CARDS_JSON, encoding="utf-8") as f:
            hn_cards = json.load(f).get("cards", [])
        hn_cards.sort(key=lambda c: c.get("age_days") or 99)

    # JS data: raw strings for clipboard + precomputed mailto query + the
    # BSD-sed tick one-liner (darwin sed -i ''), dots escaped in the pattern.
    # DO-NOT-SEND rows are deliberately absent — no mailto query, no tick
    # command exists for them anywhere in the page.
    js_data = {}
    for r in pinned + rest:
        pat = "^- \\[ \\] " + r["file"].replace(".", "\\.")
        rep = "- [x] " + r["file"]
        tick_cmd = ("sed -i '' 's|%s|%s|' "
                    "~/rick-vault/go-to-market/concierge-batch-2026-07-14/"
                    "CHECKLIST.md" % (pat, rep))
        js_data[r["num"]] = {
            "subject": r["subject"], "body": r["body"], "query": r["query"],
            "prefilled": r["prefilled"], "tick": tick_cmd,
        }

    # HN card JS data: comment text + the CHECKLIST append (targets 21+
    # convention documented in the CHECKLIST header — appended [x] lines are
    # counted by day14-gate.py exactly like draft ticks).
    hn_js = {}
    for c in hn_cards:
        target = "hn-%d-%s" % (c["story_id"], c["domain"])
        hn_js[str(c["story_id"])] = {
            "text": c["comment_text"],
            "tick": ('echo "- [x] %s — HN feedback comment hand-posted" >> '
                     "~/rick-vault/go-to-market/concierge-batch-2026-07-14/"
                     "CHECKLIST.md" % target),
        }

    review_note = extract_section(checklist_text, "## ⚠️ Review note")
    gen_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sent_count = sum(1 for v in ticks.values() if v["ticked"])

    sections = []
    if pinned:
        sections.append("<h2>Send first — address on file (send-ready)</h2>")
        sections.extend(render_row(r) for r in pinned)
    sections.append("<h2>Day 1 block (01–10) — max ~10/day, CHECKLIST rule 4</h2>")
    sections.extend(render_row(r) for r in day1)
    sections.append("<h2>Day 2 block (11–19)</h2>")
    sections.extend(render_row(r) for r in day2)
    sections.append(
        '<h2>HN feedback cards (D2 Phase-1) — ONLY after the 20 concierge '
        'sends</h2>\n<div class="banner"><b>Rules:</b> post from your '
        'PERSONAL aged HN account, 1–2/day max. The comments are value-only '
        'by construction — zero links, zero product mention (verified '
        'mechanically at generation). <b>STOP RULE:</b> any comment '
        'flagged or downvoted-dead ends the experiment immediately.</div>')
    if hn_cards:
        sections.extend(render_hn_card(c, i + 1)
                        for i, c in enumerate(hn_cards))
    else:
        sections.append('<div class="note">No HN cards on disk — run '
                        '<code>python3 scripts/concierge-dossier.py</code> '
                        'then regenerate this page.</div>')
    if dns:
        sections.append("<h2>⛔ DO NOT SEND — synthetic test data, audit only</h2>")
        sections.extend(render_dns_row(r) for r in dns)

    page = """<!DOCTYPE html>
<!-- LOCAL ONLY — real prospect emails inside. Never publish, never
     Artifact, never push. (demand-systems spec 2026-07-19, D3 guardrail) -->
<html lang="en">
<head>
<meta charset="utf-8">
<title>Concierge send-console — batch 2026-07-14 (LOCAL ONLY)</title>
<style>
  body { font: 14px/1.45 -apple-system, Helvetica, sans-serif; margin: 1.5em auto; max-width: 60em; padding: 0 1em; color: #1a1a1a; background: #fafafa; }
  h1 { font-size: 1.3em; } h2 { font-size: 1.05em; margin-top: 1.6em; }
  .banner { border: 2px solid #b00; background: #fff3f3; padding: .7em 1em; border-radius: 6px; margin-bottom: 1em; }
  .banner b { color: #b00; }
  .infobox { border: 1px solid #ccc; background: #fff; padding: .7em 1em; border-radius: 6px; margin-bottom: 1em; white-space: pre-wrap; font-size: .92em; }
  .row { border: 1px solid #ddd; background: #fff; border-radius: 6px; padding: .8em 1em; margin: .8em 0; }
  .row.sent { opacity: .45; background: #f0f0f0; }
  .row.dns { border: 2px solid #b00; background: #fff3f3; }
  .dns-badge { color: #b00; font-weight: bold; }
  .dnsreason { color: #b00; }
  .rowhead { margin-bottom: .3em; }
  .badge { background: #eef; border: 1px solid #99c; border-radius: 4px; padding: 0 .4em; font-size: .85em; }
  .state { font-size: .85em; margin-left: .5em; }
  .sent-badge { color: #060; font-weight: bold; }
  .unsent-badge { color: #b60; }
  .differs-badge { color: #b00; font-weight: bold; }
  .row.hn { border-left: 4px solid #47c; }
  details.dossier summary { cursor: pointer; color: #247; font-size: .92em; }
  details.dossier { margin: .4em 0; }
  .meta { font-size: .92em; margin: .15em 0; }
  .preview { background: #f6f6f6; border-left: 3px solid #ccc; padding: .4em .7em; white-space: pre-wrap; margin: .4em 0; }
  .full { background: #f6f6f6; padding: .5em .7em; white-space: pre-wrap; }
  .btns { margin: .5em 0 .2em; display: flex; flex-wrap: wrap; gap: .4em; }
  button { cursor: pointer; padding: .3em .7em; border: 1px solid #888; border-radius: 4px; background: #f2f2f2; }
  button.primary { background: #dbe9ff; border-color: #47c; font-weight: bold; }
  button.copied { background: #cfc; }
  .addr { width: 22em; padding: .25em .4em; }
  .note { font-size: .85em; color: #555; margin-top: .3em; }
  .note.warn { color: #b00; }
  .local { float: right; font-size: .8em; color: #777; }
  .prefilled { color: #060; }
  code { background: #eee; padding: 0 .25em; }
</style>
</head>
<body>
<h1>Concierge send-console — batch 2026-07-14</h1>
<div class="banner">
<b>LOCAL ONLY — this file contains real prospect emails. Never publish,
upload, or share it.</b><br>
<b>This page is a keyboard, NOT a ledger.</b> The <code>[x]</code> ticks in
<code>CHECKLIST.md</code> are the ONLY machine-readable record of a send —
<code>day14-gate.py</code> counts them every heartbeat. After EVERY send,
tick the draft's box in CHECKLIST.md (each row's [Copy tick command] button
gives you a one-line terminal paste that does exactly that). Row checkboxes
here are cosmetic localStorage notes and feed nothing.
</div>
<div class="infobox">Generated %s from the drafts on disk — a snapshot, not
live. Re-run <code>python3 scripts/concierge-console.py</code> after any
draft or CHECKLIST edit. State at generation: %d/%d ticked sent (%d
DO-NOT-SEND draft(s) excluded from sending, listed dead at the bottom).

Send from YOUR mailbox and YOUR accounts (CHECKLIST rule 1). If [Open email]
opens Mail.app but you want Gmail, set the mailto handler once (Gmail &gt;
settings icon in the address bar, or Mail.app &gt; Settings &gt; General &gt;
Default email reader) — otherwise use the copy buttons. Before each send run
the suppression check (any hit = skip, mark the row skipped in CHECKLIST.md).
DM channels: drop the subject, open straight with the body (rule 5).</div>
<div class="infobox"><b>⚠️ Review note — LinguaLive claim (verbatim from
CHECKLIST.md):</b>

%s</div>
%s
%s
<script>
var D = %s;
var B = %s;
var H = %s;
var NS = "cc-2026-07-14:";
function copyText(t, btn) {
  function ok() { if (btn) { btn.classList.add("copied");
    setTimeout(function(){ btn.classList.remove("copied"); }, 900); } }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(t).then(ok, function(){ fallbackCopy(t); ok(); });
  } else { fallbackCopy(t); ok(); }
}
function fallbackCopy(t) { // execCommand path for file:// in older engines
  var ta = document.createElement("textarea");
  ta.value = t; ta.style.position = "fixed"; ta.style.left = "-9999px";
  document.body.appendChild(ta); ta.select();
  try { document.execCommand("copy"); } finally { document.body.removeChild(ta); }
}
function getAddr(n) {
  if (D[n].prefilled) return D[n].prefilled;
  var el = document.getElementById("addr-" + n);
  return el ? el.value.trim() : "";
}
function openMail(n) {
  var a = getAddr(n);
  if (!a) { alert("Grab the address/handle first (paste it in the row's input)."); return; }
  location.href = "mailto:" + encodeURIComponent(a) + D[n].query;
}
function copySuppression(n, btn) {
  var a = getAddr(n);
  if (!a) { alert("Grab the address first — the check needs it."); return; }
  copyText('grep -i "' + a + '" ~/rick-vault/mailbox/suppression.txt', btn);
}
function saveAddr(n) {
  localStorage.setItem(NS + "addr:" + n,
    document.getElementById("addr-" + n).value);
}
function saveDone(n) {
  localStorage.setItem(NS + "done:" + n,
    document.getElementById("done-" + n).checked ? "1" : "");
}
// restore localStorage state (addresses + cosmetic done marks)
Object.keys(D).forEach(function(n) {
  var el = document.getElementById("addr-" + n);
  if (el) el.value = localStorage.getItem(NS + "addr:" + n) || "";
  var dn = document.getElementById("done-" + n);
  if (dn && !dn.disabled) dn.checked = !!localStorage.getItem(NS + "done:" + n);
});
</script>
</body>
</html>
""" % (html.escape(gen_ts), sent_count, EXPECTED_DRAFTS, len(dns),
       html.escape(review_note),
       ('<div class="infobox"><b>Booking link (CONCIERGE_BOOKING_URL):</b> '
        '<a href="%s" target="_blank">%s</a> — every card has a copy '
        'button; paste it into replies.</div>'
        % (html.escape(BOOKING_URL, quote=True), html.escape(BOOKING_URL))
        if BOOKING_URL else ""),
       "\n".join(sections),
       json.dumps(js_data, ensure_ascii=True).replace("</", "<\\/"),
       json.dumps(BOOKING_URL, ensure_ascii=True),
       json.dumps(hn_js, ensure_ascii=True).replace("</", "<\\/"))

    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(page)
    print("wrote %s — %d drafts (%d ticked sent), %d pinned send-ready, "
          "%d DO-NOT-SEND, %d dossiers rendered, %d HN cards, booking link "
          "%s"
          % (OUT_HTML, len(drafts), sent_count, len(pinned), len(dns),
             len(dossiers), len(hn_cards),
             "SET" if BOOKING_URL else "unset (env CONCIERGE_BOOKING_URL)"))


if __name__ == "__main__":
    main()
