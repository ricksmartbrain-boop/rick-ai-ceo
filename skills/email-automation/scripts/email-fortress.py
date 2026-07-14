#!/usr/bin/env python3
"""Prompt-injection-aware email triage for Rick."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass


RISK_PATTERNS = {
    "prompt_injection": [
        r"ignore (all|any|the) previous",
        r"system prompt",
        r"you are chatgpt",
        r"assistant[, ]",
        r"bypass",
        r"developer instructions",
        r"reveal .*prompt",
        r"print .*secrets",
        r"run this command",
    ],
    "money_or_access": [
        r"wire transfer",
        r"bank account",
        r"seed phrase",
        r"private key",
        r"wallet",
        r"api key",
        r"password",
        r"login code",
        r"reset .*password",
        r"grant access",
        r"share credentials",
        r"refund .*different",
    ],
    "attachment_risk": [
        r"open the attachment",
        r"download the file",
        r"see attached invoice",
        r"macro",
        r"enable content",
    ],
}

CATEGORY_RULES = {
    "BILLING": [r"refund", r"receipt", r"invoice", r"charge", r"payment", r"billing", r"subscription"],
    "SUPPORT": [r"bug", r"issue", r"error", r"broken", r"not working", r"can't access", r"cannot access", r"login"],
    "SALES_INQUIRY": [r"pricing", r"quote", r"interested", r"book a demo", r"hire", r"consulting", r"buy"],
    "PARTNERSHIP": [r"partnership", r"collab", r"affiliate", r"integration", r"sponsor", r"joint venture"],
    "NEWSLETTER_REPLY": [r"newsletter", r"substack", r"beehiiv", r"edition", r"post you wrote", r"article"],
    "SPAM_MARKETING": [r"seo", r"backlinks?", r"guest post", r"lead generation", r"cold email", r"marketing agency", r"web design"],
    "FIVERR": [r"fiverr", r"fiverr\.com", r"gig", r"order.*fiverr"],
}


@dataclass
class Classification:
    category: str
    risk_level: str
    reasons: list[str]
    action: str
    allow_template_reply: bool
    needs_founder_review: bool
    archive_recommended: bool


def normalized_text(sender: str, subject: str, body: str) -> str:
    return "\n".join([sender, subject, body]).lower()


def detect_risks(text: str) -> list[str]:
    findings: list[str] = []
    for label, patterns in RISK_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text, re.I):
                findings.append(label)
                break
    return sorted(set(findings))


def classify_category(text: str) -> str:
    for category, patterns in CATEGORY_RULES.items():
        for pattern in patterns:
            if re.search(pattern, text, re.I):
                return category
    if "unsubscribe" in text or "press release" in text:
        return "SPAM_MARKETING"
    return "PERSONAL"


def risk_level_for(category: str, risks: list[str]) -> str:
    if "prompt_injection" in risks or "money_or_access" in risks:
        return "critical"
    if "attachment_risk" in risks:
        return "high"
    if category in {"BILLING", "PARTNERSHIP", "PERSONAL"}:
        return "medium"
    return "low"


def policy_for(category: str, risk_level: str) -> Classification:
    if risk_level in {"critical", "high"}:
        return Classification(
            category=category,
            risk_level=risk_level,
            reasons=[],
            action="founder-review",
            allow_template_reply=False,
            needs_founder_review=True,
            archive_recommended=False,
        )

    if category == "SALES_INQUIRY":
        return Classification(category, risk_level, [], "auto-acknowledge-and-flag", True, True, False)
    if category == "SUPPORT":
        return Classification(category, risk_level, [], "auto-acknowledge-and-route", True, False, False)
    if category == "PARTNERSHIP":
        return Classification(category, risk_level, [], "flag-and-summarize", False, True, False)
    if category == "NEWSLETTER_REPLY":
        return Classification(category, risk_level, [], "log-for-content", False, False, False)
    if category == "SPAM_MARKETING":
        return Classification(category, risk_level, [], "archive-recommended", False, False, True)
    if category == "BILLING":
        return Classification(category, risk_level, [], "auto-acknowledge-and-flag", True, True, False)
    if category == "FIVERR":
        return Classification(category, risk_level, [], "route-to-fiverr-classifier", False, False, False)
    return Classification(category, risk_level, [], "founder-review", False, True, False)


def classify_email(sender: str, subject: str, body: str) -> dict:
    text = normalized_text(sender, subject, body)
    risks = detect_risks(text)
    category = classify_category(text)
    policy = policy_for(category, risk_level_for(category, risks))
    reasons = list(risks)

    if category == "SPAM_MARKETING" and policy.risk_level == "low":
        reasons.append("unsolicited-marketing-pattern")
    if category == "SUPPORT" and "money_or_access" in risks:
        reasons.append("support-request-touching-access")
    if category == "BILLING":
        reasons.append("billing-related")

    excerpt = " ".join(line.strip() for line in body.splitlines()[:8] if line.strip())[:400]

    return {
        "category": category,
        "risk_level": policy.risk_level,
        "reasons": sorted(set(reason for reason in reasons if reason)),
        "action": policy.action,
        "allow_template_reply": policy.allow_template_reply,
        "needs_founder_review": policy.needs_founder_review,
        "archive_recommended": policy.archive_recommended,
        "trusted_command_channel": False,
        "sanitized_excerpt": excerpt,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rick email fortress classifier")
    subparsers = parser.add_subparsers(dest="command", required=True)

    classify_parser = subparsers.add_parser("classify", help="Classify one email")
    classify_parser.add_argument("--from", dest="sender", default="", help="From header")
    classify_parser.add_argument("--subject", default="", help="Subject header")
    classify_parser.add_argument("--body", default="", help="Email body")
    classify_parser.add_argument("--body-file", default="", help="Read body from file")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "classify":
        body = args.body
        if args.body_file:
            with open(args.body_file, "r", encoding="utf-8") as handle:
                body = handle.read()
        print(json.dumps(classify_email(args.sender, args.subject, body), indent=2))
        return 0

    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
