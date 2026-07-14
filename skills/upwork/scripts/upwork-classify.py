#!/usr/bin/env python3
"""Upwork email classifier — routes Upwork notifications to appropriate workflows."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict


UPWORK_PATTERNS: dict[str, list[str]] = {
    "UPWORK_JOB_MATCH": [
        r"new job match",
        r"jobs matching your profile",
        r"recommended job",
        r"job alert",
        r"new job[s]? posted",
        r"jobs you might like",
    ],
    "UPWORK_INVITATION": [
        r"invited you to",
        r"job invitation",
        r"invited to apply",
        r"you'?ve been invited",
        r"client invited",
    ],
    "UPWORK_PROPOSAL_RESPONSE": [
        r"your proposal",
        r"proposal viewed",
        r"proposal declined",
        r"shortlisted",
        r"proposal was",
        r"you were shortlisted",
    ],
    "UPWORK_MESSAGE": [
        r"new message from",
        r"sent you a message",
        r"client sent you",
        r"you have a new message",
        r"message from client",
    ],
    "UPWORK_OFFER": [
        r"sent you an offer",
        r"contract offer",
        r"hire you",
        r"offer received",
        r"new offer from",
    ],
    "UPWORK_CONTRACT": [
        r"contract started",
        r"new contract",
        r"milestone funded",
        r"escrow funded",
        r"contract activated",
    ],
    "UPWORK_PAYMENT": [
        r"payment received",
        r"funds available",
        r"payment released",
        r"weekly billing",
        r"milestone payment",
    ],
    "UPWORK_REVIEW": [
        r"left you feedback",
        r"received feedback",
        r"job success",
        r"new feedback",
        r"client reviewed",
    ],
    "UPWORK_DEADLINE": [
        r"deadline approaching",
        r"contract ending",
        r"milestone due",
        r"delivery reminder",
        r"weekly limit",
    ],
}

SENDER_FILTER = re.compile(r"@upwork\.com$", re.IGNORECASE)


@dataclass
class UpworkClassification:
    category: str
    confidence: float
    matched_patterns: list[str]
    job_id: str
    client_username: str
    action: str


def extract_job_id(text: str) -> str:
    """Extract Upwork job ID (tilde-prefixed hex or numeric ID)."""
    match = re.search(r"(~[0-9a-f]{10,18})", text, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"(?:job|contract|offer)[/:#\s]*(\d{8,20})", text, re.IGNORECASE)
    return match.group(1) if match else ""


_CLIENT_FALSE_POSITIVES = frozenset({
    "upwork", "the", "your", "team", "support", "notification",
    "no-reply", "noreply", "donotreply", "system", "admin",
})


def extract_client_username(text: str) -> str:
    """Extract client username from email text."""
    match = re.search(
        r"(?:message\s+from|offer\s+from|invitation\s+from|contract\s+with|hired\s+by)\s+(\w{2,30})",
        text, re.IGNORECASE,
    )
    if match and match.group(1).lower() not in _CLIENT_FALSE_POSITIVES:
        return match.group(1)
    return ""


def classify_upwork_email(sender: str, subject: str, body: str) -> UpworkClassification:
    """Classify an Upwork email notification into a workflow action."""
    if not SENDER_FILTER.search(sender):
        return UpworkClassification(
            category="NOT_UPWORK",
            confidence=1.0,
            matched_patterns=[],
            job_id="",
            client_username="",
            action="ignore",
        )

    combined = f"{subject} {body}".lower()
    best_category = "UPWORK_UNKNOWN"
    best_matches: list[str] = []
    best_score = 0

    for category, patterns in UPWORK_PATTERNS.items():
        matches = [p for p in patterns if re.search(p, combined, re.IGNORECASE)]
        if len(matches) > best_score:
            best_score = len(matches)
            best_category = category
            best_matches = matches

    job_id = extract_job_id(combined)
    client = extract_client_username(combined)
    confidence = min(1.0, best_score * 0.35) if best_score > 0 else 0.1

    action_map = {
        "UPWORK_JOB_MATCH": "queue_upwork_proposal",
        "UPWORK_INVITATION": "queue_upwork_proposal",
        "UPWORK_PROPOSAL_RESPONSE": "log_event_proposal_response",
        "UPWORK_MESSAGE": "queue_upwork_message",
        "UPWORK_OFFER": "queue_upwork_contract",
        "UPWORK_CONTRACT": "queue_upwork_contract",
        "UPWORK_PAYMENT": "log_event_payment",
        "UPWORK_REVIEW": "log_event_review",
        "UPWORK_DEADLINE": "alert_deadline",
        "UPWORK_UNKNOWN": "flag_for_review",
    }

    return UpworkClassification(
        category=best_category,
        confidence=confidence,
        matched_patterns=best_matches,
        job_id=job_id,
        client_username=client,
        action=action_map.get(best_category, "flag_for_review"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify Upwork email notifications")
    parser.add_argument("--sender", required=True, help="Email sender address")
    parser.add_argument("--subject", required=True, help="Email subject line")
    parser.add_argument("--body", default="", help="Email body text")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    result = classify_upwork_email(args.sender, args.subject, args.body)

    if args.json:
        print(json.dumps(asdict(result), indent=2))
    else:
        print(f"Category: {result.category}")
        print(f"Confidence: {result.confidence:.1%}")
        print(f"Action: {result.action}")
        if result.job_id:
            print(f"Job ID: {result.job_id}")
        if result.client_username:
            print(f"Client: {result.client_username}")
        if result.matched_patterns:
            print(f"Matched: {', '.join(result.matched_patterns)}")

    sys.exit(0 if result.category != "NOT_UPWORK" else 1)


if __name__ == "__main__":
    main()
