#!/usr/bin/env python3
"""
prospect-mine.py — source + score cold ICP prospects for Rick's growth machine v2.

Deterministic, file-based, no external API calls. Reads existing lead sources,
dedupes against the pipeline, scores each NEW lead 0-100 on ICP fit, and writes
the top-scored qualified prospects (score>=60) to
~/rick-vault/projects/outreach/qualified-prospects.jsonl.

Scoring (max 100):
  +40  vertical match (med spa / derm / PT / agency / coach / financial advisor)
  +20  has email
  +20  not yet contacted (not in pipeline.jsonl)
  +20  business name present

Usage:
  python3 scripts/prospect-mine.py
  python3 scripts/prospect-mine.py --seed-city "Tampa"
"""
import os
import sys
import json
import argparse
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import attribution  # noqa: E402

VAULT = os.path.expanduser("~/rick-vault")
ROAST_LEADS = os.path.join(VAULT, "projects", "outreach", "roast-leads.jsonl")
PIPELINE = os.path.join(VAULT, "logs", "pipeline.jsonl")
OUT_FILE = os.path.join(VAULT, "projects", "outreach", "qualified-prospects.jsonl")

OUTPUT_CAP = 25
QUALIFY_THRESHOLD = 60

VERTICAL_TERMS = [
    "med spa", "medspa", "med-spa",
    "derm", "dermatolog",
    "physical therap", " pt ", "physiotherap",
    "agency", "marketing agency",
    "coach", "coaching",
    "financial advisor", "financial planning", "wealth",
]


def _read_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        sys.stderr.write("read %s WARN: %s\n" % (path, str(e)[:160]))
    return out


def _norm_email(obj):
    return (obj.get("email") or "").strip().lower()


def _biz(obj):
    return (obj.get("company") or obj.get("business") or obj.get("business_name") or "").strip()


def _vertical_text(obj):
    parts = [
        obj.get("industry") or "",
        obj.get("category") or "",
        obj.get("vertical") or "",
        _biz(obj),
        obj.get("signal") or "",
    ]
    return " ".join(p for p in parts if p).lower()


def has_vertical(obj):
    txt = " " + _vertical_text(obj) + " "
    return any(term in txt for term in VERTICAL_TERMS)


def score_lead(obj, contacted_emails):
    score = 0
    reasons = []
    if has_vertical(obj):
        score += 40
        reasons.append("vertical")
    email = _norm_email(obj)
    if email:
        score += 20
        reasons.append("email")
    if email and email not in contacted_emails:
        score += 20
        reasons.append("uncontacted")
    elif not email:
        # no email can't be contacted; give the uncontacted credit conservatively
        score += 20
        reasons.append("uncontacted")
    if _biz(obj):
        score += 20
        reasons.append("biz_name")
    return score, reasons


def main():
    p = argparse.ArgumentParser(description="Mine + score cold ICP prospects")
    p.add_argument("--seed-city", default=None)
    args = p.parse_args()

    pipeline = _read_jsonl(PIPELINE)
    contacted_emails = set(_norm_email(o) for o in pipeline if _norm_email(o))

    sources = _read_jsonl(ROAST_LEADS) + pipeline
    # Already-qualified emails to avoid re-emitting.
    existing_qualified = set(_norm_email(o) for o in _read_jsonl(OUT_FILE) if _norm_email(o))

    # Dedupe candidates by email (keep first); keyless entries kept by biz+source.
    seen_keys = set()
    candidates = []
    for o in sources:
        email = _norm_email(o)
        key = email or ("biz:" + _biz(o).lower())
        if not key or key == "biz:":
            continue
        if key in seen_keys:
            continue
        seen_keys.add(key)
        candidates.append(o)

    scored = []
    for o in candidates:
        email = _norm_email(o)
        # NEW = not already in qualified output and (if has email) not contacted.
        if email and email in existing_qualified:
            continue
        if email and email in contacted_emails:
            continue
        score, reasons = score_lead(o, contacted_emails)
        if score < QUALIFY_THRESHOLD:
            continue
        rec = {
            "email": email or None,
            "business": _biz(o) or None,
            "vertical": (o.get("industry") or o.get("category") or None),
            "city": o.get("city") or args.seed_city,
            "score": score,
            "score_reasons": reasons,
            "source": o.get("source") or "prospect-mine",
            "scored_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        scored.append(rec)

    # Deterministic ordering: score desc, then business name, then email.
    scored.sort(key=lambda r: (-r["score"], (r.get("business") or ""), (r.get("email") or "")))
    top = scored[:OUTPUT_CAP]

    if not top:
        print("prospect-mine: 0 new qualified prospects (score>=%d) — nothing to do" % QUALIFY_THRESHOLD)
        return 0

    try:
        os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
        with open(OUT_FILE, "a") as f:
            for rec in top:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        sys.stderr.write("prospect-mine write WARN: %s\n" % (str(e)[:200]))

    for rec in top:
        attribution.log_event(
            stage="capture",
            channel="prospect-mine",
            asset_id="prospect-mine",
            src="cold-icp",
            lead=rec.get("email") or rec.get("business"),
            detail="qualified score=%d %s" % (rec["score"], ",".join(rec["score_reasons"])),
            amount=0,
        )

    print("prospect-mine: %d new qualified prospects (score>=%d) written to %s" % (
        len(top), QUALIFY_THRESHOLD, OUT_FILE))
    return 0


if __name__ == "__main__":
    sys.exit(main())
