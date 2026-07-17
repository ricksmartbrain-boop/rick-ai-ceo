#!/usr/bin/env python3
"""
concierge-console.py — one-shot generator for the concierge hand-send console.

Reads ~/rick-vault/go-to-market/concierge-batch-2026-07-14/ (CHECKLIST.md +
the 20 NN-*.md drafts) and writes send-console.html into that same directory:
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

Usage: python3 scripts/concierge-console.py
Re-run after any draft or CHECKLIST edit — the page is a snapshot.
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
EXPECTED_DRAFTS = 20
# Practical mailto URL limit: above this many URL-encoded body chars the
# mailto carries subject only and the row directs to the copy button.
MAILTO_BODY_LIMIT = 1800

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
    """filename -> True if ticked [x] in CHECKLIST.md right now."""
    ticks = {}
    for m in re.finditer(r"^- \[( |x|X)\] (\S+\.md)\s*$",
                         checklist_text, re.MULTILINE):
        ticks[m.group(2)] = m.group(1).lower() == "x"
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


def build_rows(drafts, ticks):
    """Per-draft render/JS data. Order: warm prefilled first, then 01-10, 11-19."""
    rows = []
    for fname in sorted(drafts):
        d = drafts[fname]
        num = fname[:2]
        # The one send-ready draft (20, arjun@rtrvr.ai) has its address in
        # the To line; everyone else needs Vlad's ~30-sec handle grab.
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
            "ticked": ticks.get(fname, False),
        })
    # Pinned: prefilled-address rows (draft 20) — zero-friction, highest EV.
    pinned = [r for r in rows if r["prefilled"]]
    rest = [r for r in rows if not r["prefilled"]]
    return pinned, rest


def render_row(r):
    e = html.escape
    num = r["num"]
    classes = "row" + (" sent" if r["ticked"] else "")
    state = ('<span class="state sent-badge">SENT — ticked in CHECKLIST.md</span>'
             if r["ticked"] else
             '<span class="state unsent-badge">unsent</span>')
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
  <div class="btns">
    <button class="primary" onclick="openMail('%s')">Open email (mailto)</button>
    <button onclick="copyText(D['%s'].subject,this)">Copy subject</button>
    <button onclick="copyText(D['%s'].body,this)">Copy body</button>
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
        e(r["subject"]), e(preview), e(r["body"]), mailto_note,
        num, num, num, dm_btn, num, num, e(r["file"]))


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

    pinned, rest = build_rows(drafts, ticks)
    day1 = [r for r in rest if r["num"] <= "10"]
    day2 = [r for r in rest if r["num"] > "10"]

    # JS data: raw strings for clipboard + precomputed mailto query + the
    # BSD-sed tick one-liner (darwin sed -i ''), dots escaped in the pattern.
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

    review_note = extract_section(checklist_text, "## ⚠️ Review note")
    gen_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sent_count = sum(1 for v in ticks.values() if v)

    sections = []
    if pinned:
        sections.append("<h2>Send first — warm, address on file</h2>")
        sections.extend(render_row(r) for r in pinned)
    sections.append("<h2>Day 1 block (01–10) — max ~10/day, CHECKLIST rule 4</h2>")
    sections.extend(render_row(r) for r in day1)
    sections.append("<h2>Day 2 block (11–19)</h2>")
    sections.extend(render_row(r) for r in day2)

    page = """<!DOCTYPE html>
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
  .rowhead { margin-bottom: .3em; }
  .badge { background: #eef; border: 1px solid #99c; border-radius: 4px; padding: 0 .4em; font-size: .85em; }
  .state { font-size: .85em; margin-left: .5em; }
  .sent-badge { color: #060; font-weight: bold; }
  .unsent-badge { color: #b60; }
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
draft or CHECKLIST edit. State at generation: %d/%d ticked sent.

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
<script>
var D = %s;
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
""" % (html.escape(gen_ts), sent_count, EXPECTED_DRAFTS,
       html.escape(review_note),
       "\n".join(sections),
       json.dumps(js_data, ensure_ascii=True).replace("</", "<\\/"))

    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(page)
    print("wrote %s — %d drafts (%d ticked sent), %d pinned warm"
          % (OUT_HTML, len(drafts), sent_count, len(pinned)))


if __name__ == "__main__":
    main()
