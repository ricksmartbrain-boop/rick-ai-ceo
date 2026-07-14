#!/usr/bin/env python3
"""Fiverr email classifier — routes Fiverr notifications to appropriate workflows."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict


FIVERR_PATTERNS: dict[str, list[str]] = {
    "FIVERR_ORDER": [
        r"new order",
        r"order\s*#\w+",
        r"congratulations.*new order",
        r"order\s+confirmed",
        r"you have a new order",
    ],
    "FIVERR_MESSAGE": [
        r"new message from",
        r"buyer sent you a message",
        r"you have a new message",
        r"message from buyer",
    ],
    "FIVERR_REVIEW": [
        r"left you a review",
        r"new review",
        r"\d[\.\d]*\s*star review",
        r"buyer reviewed",
    ],
    "FIVERR_DEADLINE": [
        r"delivery due",
        r"late delivery warning",
        r"order deadline",
        r"delivery reminder",
        r"time to deliver",
    ],
    "FIVERR_CUSTOM_OFFER": [
        r"custom offer",
        r"buyer requested a quote",
        r"custom order request",
        r"quote request",
    ],
}

SENDER_FILTER = re.compile(r"@fiverr\.com$", re.IGNORECASE)


@dataclass
class FiverrClassification:
    category: str
    confidence: float
    matched_patterns: list[str]
    order_id: str
    buyer_username: str
    action: str


def extract_order_id(text: str) -> str:
    match = re.search(r"(?:order|#)\s*(FO[A-Z0-9]+|[A-Z0-9]{8,})", text, re.IGNORECASE)
    return match.group(1) if match else ""


_BUYER_FALSE_POSITIVES = frozenset({
    "fiverr", "the", "your", "team", "support", "notification",
    "order", "new", "you", "this", "that", "has", "was", "are",
})


def extract_buyer_username(text: str) -> str:
    match = re.search(
        r"(?:message\s+from|new\s+message\s+from|order\s+from|buyer[:\s]+)(\w{3,30})",
        text, re.IGNORECASE,
    )
    if match and match.group(1).lower() not in _BUYER_FALSE_POSITIVES:
        return match.group(1)
    return ""


def classify_fiverr_email(sender: str, subject: str, body: str) -> FiverrClassification:
    if not SENDER_FILTER.search(sender):
        return FiverrClassification(
            category="NOT_FIVERR",
            confidence=1.0,
            matched_patterns=[],
            order_id="",
            buyer_username="",
            action="ignore",
        )

    combined = f"{subject} {body}".lower()
    best_category = "FIVERR_UNKNOWN"
    best_matches: list[str] = []
    best_score = 0

    for category, patterns in FIVERR_PATTERNS.items():
        matches = [p for p in patterns if re.search(p, combined, re.IGNORECASE)]
        if len(matches) > best_score:
            best_score = len(matches)
            best_category = category
            best_matches = matches

    order_id = extract_order_id(combined)
    buyer = extract_buyer_username(combined)
    confidence = min(1.0, best_score * 0.4) if best_score > 0 else 0.1

    action_map = {
        "FIVERR_ORDER": "queue_fiverr_order",
        "FIVERR_MESSAGE": "queue_fiverr_inquiry",
        "FIVERR_REVIEW": "log_event_review",
        "FIVERR_DEADLINE": "alert_deadline",
        "FIVERR_CUSTOM_OFFER": "queue_fiverr_inquiry",
        "FIVERR_UNKNOWN": "flag_for_review",
    }

    return FiverrClassification(
        category=best_category,
        confidence=confidence,
        matched_patterns=best_matches,
        order_id=order_id,
        buyer_username=buyer,
        action=action_map.get(best_category, "flag_for_review"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify Fiverr email notifications")
    parser.add_argument("--sender", required=True, help="Email sender address")
    parser.add_argument("--subject", required=True, help="Email subject line")
    parser.add_argument("--body", default="", help="Email body text")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    result = classify_fiverr_email(args.sender, args.subject, args.body)

    if args.json:
        print(json.dumps(asdict(result), indent=2))
    else:
        print(f"Category: {result.category}")
        print(f"Confidence: {result.confidence:.1%}")
        print(f"Action: {result.action}")
        if result.order_id:
            print(f"Order ID: {result.order_id}")
        if result.buyer_username:
            print(f"Buyer: {result.buyer_username}")
        if result.matched_patterns:
            print(f"Matched: {', '.join(result.matched_patterns)}")

    sys.exit(0 if result.category != "NOT_FIVERR" else 1)


if __name__ == "__main__":
    main()
