#!/usr/bin/env python3
"""Stripe revenue metrics across configured Stripe accounts."""

import argparse
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone


DEFAULT_ACCOUNTS = {}


def load_stripe_key():
    env_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if env_key:
        return env_key

    for key_path in (
        os.path.expanduser("~/.config/stripe/api_key"),
        os.path.expanduser("~/.config/stripe/api_key.env"),
    ):
        if os.path.exists(key_path):
            with open(key_path, "r", encoding="utf-8") as fh:
                key = fh.read().strip()
            if key.startswith("export ") and "=" in key:
                key = key.split("=", 1)[1].strip().strip('"').strip("'")
            return key

    raise SystemExit("Missing Stripe key. Set STRIPE_SECRET_KEY or ~/.config/stripe/api_key(.env).")


def load_accounts():
    accounts_file = os.getenv("RICK_STRIPE_ACCOUNTS_FILE", "").strip()
    if accounts_file:
        resolved = os.path.expanduser(accounts_file)
        if os.path.exists(resolved):
            with open(resolved, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            return raw.get("accounts", raw)

    return DEFAULT_ACCOUNTS


def parse_period(period):
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "today":
        return today_start, now, today_start - timedelta(days=1), today_start
    if period == "yesterday":
        return (
            today_start - timedelta(days=1),
            today_start,
            today_start - timedelta(days=2),
            today_start - timedelta(days=1),
        )
    if period == "week":
        return now - timedelta(days=7), now, now - timedelta(days=14), now - timedelta(days=7)
    if period == "month":
        return now - timedelta(days=30), now, now - timedelta(days=60), now - timedelta(days=30)
    if period == "all":
        return datetime(2020, 1, 1, tzinfo=timezone.utc), now, None, None
    raise ValueError(f"Unknown period: {period}")


def fetch_charges(stripe_key, acct_id, created_gte, created_lt):
    charges = []
    url = f"https://api.stripe.com/v1/charges?limit=100&created[gte]={int(created_gte.timestamp())}"
    if created_lt:
        url += f"&created[lt]={int(created_lt.timestamp())}"
    headers = ["-H", f"Authorization: Bearer {stripe_key}"]
    if acct_id:
        headers += ["-H", f"Stripe-Account: {acct_id}"]

    while url:
        result = subprocess.run(["curl", "-s", "-g", url] + headers, capture_output=True, text=True, check=False)
        data = json.loads(result.stdout or "{}")
        charges.extend(data.get("data", []))
        url = None
        if data.get("has_more") and charges:
            url = (
                "https://api.stripe.com/v1/charges"
                f"?limit=100&starting_after={charges[-1]['id']}"
                f"&created[gte]={int(created_gte.timestamp())}"
            )
            if created_lt:
                url += f"&created[lt]={int(created_lt.timestamp())}"
    return charges


def summarize(charges):
    gross = sum(c["amount"] for c in charges if c.get("status") == "succeeded") / 100
    refunds = sum(c.get("amount_refunded", 0) for c in charges) / 100
    net = gross - refunds
    return {
        "gross": round(gross, 2),
        "refunds": round(refunds, 2),
        "net": round(net, 2),
        "count": sum(1 for c in charges if c.get("status") == "succeeded"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", default="today", choices=["today", "yesterday", "week", "month", "all"])
    parser.add_argument("--json", action="store_true", help="Compatibility flag. Output is always JSON.")
    args = parser.parse_args()

    stripe_key = load_stripe_key()
    accounts = load_accounts()
    if not accounts:
        raise SystemExit(
            "No Stripe accounts configured. Set RICK_STRIPE_ACCOUNTS_FILE to a JSON file like config/stripe-accounts.example.json."
        )
    start, current_end, prev_start, prev_end = parse_period(args.period)

    results = {}
    for name, raw_account in accounts.items():
        if isinstance(raw_account, str):
            account = {"account_id": raw_account, "product": name}
        else:
            account = raw_account

        current = summarize(fetch_charges(stripe_key, account.get("account_id"), start, current_end))
        payload = {
            "product": account.get("product", name),
            "current": current,
        }

        if prev_start:
            previous = summarize(fetch_charges(stripe_key, account.get("account_id"), prev_start, prev_end))
            growth = ((current["net"] - previous["net"]) / previous["net"] * 100) if previous["net"] else 0
            payload["previous"] = previous
            payload["growth_pct"] = round(growth, 1)

        results[name] = payload

    total_current = {
        "gross": round(sum(r["current"]["gross"] for r in results.values()), 2),
        "refunds": round(sum(r["current"]["refunds"] for r in results.values()), 2),
        "net": round(sum(r["current"]["net"] for r in results.values()), 2),
        "count": sum(r["current"]["count"] for r in results.values()),
    }
    results["_total"] = {"period": args.period, "current": total_current}

    if prev_start:
        total_previous = {
            "gross": round(sum(r["previous"]["gross"] for r in results.values()), 2),
            "refunds": round(sum(r["previous"]["refunds"] for r in results.values()), 2),
            "net": round(sum(r["previous"]["net"] for r in results.values()), 2),
            "count": sum(r["previous"]["count"] for r in results.values()),
        }
        growth = ((total_current["net"] - total_previous["net"]) / total_previous["net"] * 100) if total_previous["net"] else 0
        results["_total"]["previous"] = total_previous
        results["_total"]["growth_pct"] = round(growth, 1)

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
