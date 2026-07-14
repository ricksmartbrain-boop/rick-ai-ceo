#!/usr/bin/env python3
"""
attribution.py — shared attribution ledger module + CLI for Rick's growth machine v2.

The ledger is a JSONL file at ~/rick-vault/control/attribution-ledger.jsonl, one
JSON object per line:

    {"ts","stage","channel","asset_id","src","lead","detail","amount"}

Stages: capture, reply, call_booked, call_done, close.

Importable by the other v2 scripts:

    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    import attribution
    attribution.log_event("capture", "linkedin", src="li-receipt-20260611", lead="x@y.com")

CLI:
    python3 scripts/attribution.py log --stage capture --channel linkedin \
        --src li-receipt-20260611 --lead someone@x.com
    python3 scripts/attribution.py report --days 7
"""
import os
import sys
import json
import argparse
import datetime

VAULT = os.path.expanduser("~/rick-vault")
LEDGER = os.path.join(VAULT, "control", "attribution-ledger.jsonl")

STAGES = ("capture", "reply", "call_booked", "call_done", "close")


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_event(stage, channel, asset_id=None, src=None, lead=None, detail=None, amount=0):
    """Append a single attribution event to the shared ledger.

    Tolerant: creates the control/ dir if missing, never raises on a normal
    write. Returns the event dict that was written (or would have been).
    """
    evt = {
        "ts": _now_iso(),
        "stage": stage,
        "channel": channel,
        "asset_id": asset_id,
        "src": src,
        "lead": lead,
        "detail": detail,
        "amount": _coerce_amount(amount),
    }
    try:
        os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
        with open(LEDGER, "a") as f:
            f.write(json.dumps(evt, ensure_ascii=False) + "\n")
    except Exception as e:
        # Never let attribution logging crash a caller.
        sys.stderr.write("attribution.log_event WARN: %s\n" % (str(e)[:200]))
    return evt


def _coerce_amount(amount):
    try:
        return round(float(amount or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def read_events(days=None):
    """Yield ledger events as dicts. If days is set, only events within the
    last `days` days (by ts). Tolerant of a missing/garbled ledger."""
    if not os.path.exists(LEDGER):
        return []
    cutoff = None
    if days is not None:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    out = []
    try:
        with open(LEDGER) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if cutoff is not None:
                    ts = _parse_ts(evt.get("ts"))
                    if ts is None or ts < cutoff:
                        continue
                out.append(evt)
    except Exception as e:
        sys.stderr.write("attribution.read_events WARN: %s\n" % (str(e)[:200]))
    return out


def _parse_ts(ts):
    if not ts:
        return None
    try:
        s = ts.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except Exception:
        return None


def report(days=7):
    events = read_events(days=days)
    by_stage = {}
    by_channel = {}
    total_amount = 0.0
    for e in events:
        st = e.get("stage") or "?"
        ch = e.get("channel") or "?"
        by_stage[st] = by_stage.get(st, 0) + 1
        by_channel[ch] = by_channel.get(ch, 0) + 1
        total_amount += _coerce_amount(e.get("amount"))
    return {
        "days": days,
        "total_events": len(events),
        "by_stage": by_stage,
        "by_channel": by_channel,
        "total_amount": round(total_amount, 2),
    }


def _cmd_log(args):
    if args.stage not in STAGES:
        sys.stderr.write("WARN: stage %r not in known stages %s\n" % (args.stage, STAGES))
    evt = log_event(
        stage=args.stage,
        channel=args.channel,
        asset_id=args.asset_id,
        src=args.src,
        lead=args.lead,
        detail=args.detail,
        amount=args.amount,
    )
    print("logged %s/%s lead=%s amount=%s -> %s" % (
        evt["stage"], evt["channel"], evt.get("lead"), evt["amount"], LEDGER))


def _cmd_report(args):
    r = report(days=args.days)
    print("attribution report — last %d days (%d events, $%.2f total)" % (
        r["days"], r["total_events"], r["total_amount"]))
    print("  by stage:")
    if r["by_stage"]:
        for k in STAGES:
            if k in r["by_stage"]:
                print("    %-12s %d" % (k, r["by_stage"][k]))
        for k, v in sorted(r["by_stage"].items()):
            if k not in STAGES:
                print("    %-12s %d" % (k, v))
    else:
        print("    (none)")
    print("  by channel:")
    if r["by_channel"]:
        for k, v in sorted(r["by_channel"].items(), key=lambda kv: -kv[1]):
            print("    %-16s %d" % (k, v))
    else:
        print("    (none)")


def main(argv=None):
    p = argparse.ArgumentParser(description="Attribution ledger module + CLI")
    sub = p.add_subparsers(dest="cmd")

    pl = sub.add_parser("log", help="append an event to the ledger")
    pl.add_argument("--stage", required=True)
    pl.add_argument("--channel", required=True)
    pl.add_argument("--asset-id", dest="asset_id", default=None)
    pl.add_argument("--src", default=None)
    pl.add_argument("--lead", default=None)
    pl.add_argument("--detail", default=None)
    pl.add_argument("--amount", default=0)
    pl.set_defaults(func=_cmd_log)

    pr = sub.add_parser("report", help="print stage/channel/$ counts")
    pr.add_argument("--days", type=int, default=7)
    pr.set_defaults(func=_cmd_report)

    args = p.parse_args(argv)
    if not getattr(args, "cmd", None):
        # Default to a 7-day report so a bare run is a clean, useful no-op.
        _cmd_report(argparse.Namespace(days=7))
        return 0
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
