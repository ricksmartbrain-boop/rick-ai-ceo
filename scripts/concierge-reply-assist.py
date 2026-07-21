#!/usr/bin/env python3
"""
concierge-reply-assist.py — reply-moment draft assistant for the hand-send
concierge batch (rick-vault/go-to-market/concierge-batch-2026-07-14/).

WHY THIS EXISTS: the 20 concierge messages are hand-sent by Vlad from his own
mailbox / DMs, so a prospect's reply lands in HIS inbox — Rick never sees it
(imap-watcher only reads Rick's own addresses). The dossier bakes ONE
anticipated objection; a real reply often raises a different one. This tool
takes the ACTUAL reply text plus the draft + verified dossier and drafts a
tailored, in-voice response for Vlad to review and send by hand. It multiplies
the only channel that has ever produced a deal_close — the hand-send session —
without adding a send path, a cron, or an autonomous outbound step.

CONTRACT (matches concierge-dossier.py's discipline):
  - Reads only: NN-*.md (original draft) + NN-dossier.md (verified facts,
    objection+counter, opener, follow-up timing).
  - ONE runtime.llm route='writing' call (Sonnet / Anthropic-first per the
    owner-facing-comms rule). Canned budget fallback = hard failure, never a
    silent stub.
  - Facts are DOSSIER-ONLY. The model is told to never invent product facts,
    metrics, prices, or guarantee terms; anything the dossier can't support is
    surfaced as an explicit [NEEDS VLAD] gap, never fabricated.
  - Offer terms are injected from OFFER (a constant, the live site's terms) —
    not left to the model to remember.
  - Deterministic post-checks flag any $ amount, guarantee number, or URL the
    draft introduces outside the allowed set, printed loudly with the output.
  - Writes a LOCAL-ONLY draft file (real prospect context) and prints it.
    SENDS NOTHING, queues nothing, writes no ledger. Vlad copies + sends.

Usage:
  python3 scripts/concierge-reply-assist.py --num 05 --reply-file reply.txt
  pbpaste | python3 scripts/concierge-reply-assist.py --num 05      # reply on stdin
Exit: nonzero on load error or canned-fallback (fail loud); 0 with the draft
written even when post-checks flag items for review (the draft is still useful).
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import re
import sys
from pathlib import Path

BATCH_DIR = Path.home() / "rick-vault" / "go-to-market" / "concierge-batch-2026-07-14"
FALLBACK = "REPLY-ASSIST-FALLBACK-DO-NOT-USE"

# Load founder-sourcer.py for its import side effects only: rick.env
# setdefault-load + sys.path setup so `runtime` is importable (same pattern as
# concierge-dossier.py). It fetches nothing at import time.
_FS_PATH = Path(__file__).resolve().parent / "founder-sourcer.py"
_spec = importlib.util.spec_from_file_location("founder_sourcer", _FS_PATH)
_fs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fs)

# The live offer, verbatim from the site (install-strategy-2026-07-20, owner
# Variant B). Injected so the model never has to remember prices/terms.
OFFER = (
    "meetrick Managed pilot: free first week, then $249 for month 1, then "
    "$499/mo. Outcome-guaranteed: 100 net-new signups/trials in 14 days or "
    "your money back. Book/apply: https://meetrick.ai/pilot"
)

# Post-check allow-lists: anything outside these is surfaced for Vlad's eye.
ALLOWED_PRICES = {"$249", "$499", "$2,500", "$2500"}
ALLOWED_URL_HOSTS = ("meetrick.ai",)  # + the prospect's own domain, added at runtime
PRICE_RE = re.compile(r"\$\s?\d[\d,]*")
URL_RE = re.compile(r"https?://[^\s)>\]]+", re.IGNORECASE)
# The guarantee is "100 net-new signups/trials in 14 days". Only check numbers
# inside a sentence that actually makes a guarantee-style claim, so the free
# week's own "7 days" (a different, correct number) doesn't false-flag.
GUARANTEE_CTX_RE = re.compile(r"net-new|signups|trials|guarantee|money back", re.IGNORECASE)
GUARANTEE_COUNT_RE = re.compile(r"\b(\d+)\s*(?:net-new|signups|trials)\b", re.IGNORECASE)
GUARANTEE_WINDOW_RE = re.compile(r"\b(\d+)\s*days\b", re.IGNORECASE)


def now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def load_batch_file(num: str, kind: str) -> tuple[Path, str]:
    """Find NN-*.md (kind='draft') or NN-dossier.md (kind='dossier')."""
    if kind == "dossier":
        path = BATCH_DIR / f"{num}-dossier.md"
        if not path.exists():
            raise FileNotFoundError(f"no dossier for {num}: {path}")
        return path, path.read_text(encoding="utf-8")
    # draft: NN-<slug>.md but not NN-dossier.md
    matches = sorted(p for p in BATCH_DIR.glob(f"{num}-*.md")
                     if not p.name.endswith("-dossier.md"))
    if not matches:
        raise FileNotFoundError(f"no draft file for {num} in {BATCH_DIR}")
    return matches[0], matches[0].read_text(encoding="utf-8")


def prospect_domain(draft_text: str) -> str | None:
    """Best-effort prospect domain from the draft's To/Channel lines, so their
    own URL is allowed in the reply without a false flag."""
    for m in URL_RE.finditer(draft_text):
        host = re.sub(r"^https?://", "", m.group(0)).split("/")[0].lower()
        if not host.startswith("producthunt.") and "meetrick.ai" not in host:
            return host
    return None


def build_prompt(num: str, draft_text: str, dossier_text: str, reply_text: str) -> str:
    return f"""You are Rick, an autonomous AI CEO (meetrick.ai) with a dry, direct, \
lightly playful voice — a founder talking to a founder, never a salesperson. \
A prospect you cold-messaged has REPLIED. Draft Vlad's response for him to \
review and send by hand.

THE OFFER (use these terms EXACTLY — never alter a price, number, or guarantee):
{OFFER}

YOUR ORIGINAL MESSAGE TO THEM (draft {num}):
\"\"\"
{draft_text.strip()}
\"\"\"

VERIFIED DOSSIER (the ONLY product facts you may assert about them — every \
quote here was verified verbatim against their live site; if the dossier says \
facts are unavailable, you have NONE and must not invent any):
\"\"\"
{dossier_text.strip()}
\"\"\"

THEIR ACTUAL REPLY:
\"\"\"
{reply_text.strip()}
\"\"\"

Write the response. Rules:
- Address what THEY actually said — their specific question or objection, not \
the one the dossier anticipated (use the dossier's objection+counter only if \
it genuinely fits their reply).
- Never invent a product fact, metric, customer, price, or guarantee term. If \
answering well needs something the dossier and offer don't give you, write \
[NEEDS VLAD: <what's missing>] inline instead of guessing.
- Propose ONE concrete next step (a short call or starting the free pilot week), \
low-friction. Only the offer's own URL, if any.
- Keep it tight: a real reply a busy founder would actually send. No headers, \
no sign-off block beyond a simple "— Vlad". Plain text.

Output ONLY the reply body."""


def post_checks(draft: str, allowed_hosts: tuple[str, ...]) -> list[str]:
    """Deterministic guardrails — surface anything the model introduced that a
    human must verify. Warnings, not failures (the draft is still useful)."""
    flags: list[str] = []
    for m in PRICE_RE.finditer(draft):
        # rstrip trailing punctuation the greedy [\d,]* can absorb ("$249," in
        # "$249, then") without touching internal separators ("$2,500").
        norm = m.group(0).replace(" ", "").rstrip(",.")
        if norm not in ALLOWED_PRICES:
            flags.append(f"stray price {m.group(0)!r} (offer prices: $249/$499)")
    for m in URL_RE.finditer(draft):
        host = re.sub(r"^https?://", "", m.group(0)).split("/")[0].lower()
        if not any(host == h or host.endswith("." + h) for h in allowed_hosts):
            flags.append(f"URL to {host!r} not in the offer/prospect domains")
    for sentence in re.split(r"(?<=[.!?])\s+", draft):
        if not GUARANTEE_CTX_RE.search(sentence):
            continue  # not a guarantee claim — bare "7 days" (free week) is fine
        for m in GUARANTEE_COUNT_RE.finditer(sentence):
            if m.group(1) != "100":
                flags.append(f"guarantee count {m.group(0)!r} (offer is 100)")
        for m in GUARANTEE_WINDOW_RE.finditer(sentence):
            if m.group(1) != "14":
                flags.append(f"guarantee window {m.group(0)!r} (offer is 14 days)")
    if "[NEEDS VLAD" in draft:
        flags.append("draft left a [NEEDS VLAD] gap — fill it before sending")
    return flags


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--num", required=True, help="draft number, e.g. 05")
    ap.add_argument("--reply-file", help="file with the prospect's reply; omit to read stdin")
    ap.add_argument("--out", help="output path; default writes beside the draft")
    args = ap.parse_args()

    num = args.num.zfill(2)
    if args.reply_file:
        reply_text = Path(args.reply_file).read_text(encoding="utf-8")
    else:
        reply_text = sys.stdin.read()
    if not reply_text.strip():
        print("ERROR: empty reply text (pass --reply-file or pipe it on stdin)", file=sys.stderr)
        return 2

    try:
        draft_path, draft_text = load_batch_file(num, "draft")
        _, dossier_text = load_batch_file(num, "dossier")
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    allowed_hosts = ALLOWED_URL_HOSTS + tuple(d for d in [prospect_domain(draft_text)] if d)

    from runtime.llm import generate_text
    result = generate_text("writing", build_prompt(num, draft_text, dossier_text, reply_text),
                           FALLBACK, force_fresh=True)
    body = (result.content or "").strip()
    if result.mode == "fallback" or FALLBACK in body:
        print(f"ERROR: writing route returned canned fallback (mode={result.mode}) — "
              "budget-capped or provider dead; not writing a stub draft", file=sys.stderr)
        return 1

    flags = post_checks(body, allowed_hosts)
    out_path = Path(args.out) if args.out else (
        BATCH_DIR / f"{num}-reply-draft-{datetime.datetime.now():%Y%m%dT%H%M%S}.md")
    header = (
        f"<!-- LOCAL ONLY — real prospect reply context, never publish/Artifact/push -->\n"
        f"# Reply draft — {draft_path.name} — {now_iso()} (model {result.model})\n\n"
        f"**REVIEW BEFORE SENDING. Rick drafted this; Vlad sends by hand.**\n\n"
    )
    if flags:
        header += "**⚠ post-checks flagged — verify each before sending:**\n" + \
                  "".join(f"- {f}\n" for f in flags) + "\n"
    out_path.write_text(header + "---\n\n" + body + "\n", encoding="utf-8")

    print(f"\n=== reply draft for {draft_path.name} (model {result.model}) ===\n")
    print(body)
    if flags:
        print("\n⚠ POST-CHECK FLAGS (verify before sending):", file=sys.stderr)
        for f in flags:
            print(f"  - {f}", file=sys.stderr)
    print(f"\n[written to {out_path}]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
