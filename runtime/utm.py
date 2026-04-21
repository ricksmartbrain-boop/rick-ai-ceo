#!/usr/bin/env python3
"""UTM helpers — stamp outbound meetrick.ai URLs with attribution params.

Every outbound touch across channels passes through a formatter. Each
formatter calls stamp_urls_in_text(body, channel, lane, msg_id) before
dispatching, so any meetrick.ai link embedded in the body gets the right
utm_source/utm_medium/utm_campaign/utm_content. Analytics (GA4 / GSC /
self-hosted attribution endpoint) then correlates install + conversion
events back to the originating channel.

Pre-existing UTM params in hand-crafted URLs WIN — stamp_urls_in_text
uses dict.setdefault so the caller's intent is preserved.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Optional

# Regex to locate any meetrick.ai URL in free-form text. Excludes common
# markdown/html/punctuation terminators so `(https://meetrick.ai)` doesn't
# pull the close-paren into the match.
MEETRICK_URL_RE = re.compile(r"https?://meetrick\.ai[^\s)>\]}\"'`]*")

# Default UTM lane per channel when caller doesn't specify one.
DEFAULT_LANE_BY_CHANNEL = {
    "email": "outbound",
    "linkedin": "outbound",
    "moltbook": "distribution",
    "moltbook_post": "distribution",
    "reddit": "distribution",
    "reddit_post": "distribution",
    "threads": "distribution",
    "instagram": "distribution",
    "x": "distribution",
    "newsletter": "lifecycle",
}


def _lane_for(channel: str, lane: Optional[str]) -> str:
    if lane:
        return lane
    return DEFAULT_LANE_BY_CHANNEL.get(channel, "outbound")


def build_install_url(
    channel: str,
    lane: Optional[str] = None,
    msg_id: Optional[str] = None,
    campaign: Optional[str] = None,
    path: str = "/",
) -> str:
    """Return a meetrick.ai install URL with UTM params applied."""
    params = {
        "utm_source": channel,
        "utm_medium": _lane_for(channel, lane),
        "utm_campaign": campaign or "rick-auto",
    }
    if msg_id:
        params["utm_content"] = f"msg_{msg_id}"
    query = urllib.parse.urlencode(params)
    if not path.startswith("/"):
        path = "/" + path
    return f"https://meetrick.ai{path}?{query}"


def stamp_urls_in_text(
    text: str,
    channel: str,
    lane: Optional[str] = None,
    msg_id: Optional[str] = None,
    campaign: Optional[str] = None,
) -> str:
    """Append UTM params to every meetrick.ai URL found in text.

    - Pre-existing UTM params WIN (dict.setdefault semantics).
    - Non-meetrick URLs are untouched.
    - A string with no meetrick URLs is returned unchanged — formatter
      can call this unconditionally with no side effects.
    """
    if not text or "meetrick.ai" not in text:
        return text

    lane_val = _lane_for(channel, lane)
    campaign_val = campaign or "rick-auto"

    def _rewrite(match: re.Match) -> str:
        url = match.group(0)
        try:
            parsed = urllib.parse.urlparse(url)
        except ValueError:
            return url
        existing = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        existing.setdefault("utm_source", channel)
        existing.setdefault("utm_medium", lane_val)
        existing.setdefault("utm_campaign", campaign_val)
        if msg_id:
            existing.setdefault("utm_content", f"msg_{msg_id}")
        new_query = urllib.parse.urlencode(existing)
        rebuilt = parsed._replace(query=new_query)
        return urllib.parse.urlunparse(rebuilt)

    return MEETRICK_URL_RE.sub(_rewrite, text)


def build_tracking_pixel_url(rick_id: str, msg_id: Optional[str] = None) -> str:
    """Return a 1x1-pixel URL for email open tracking.

    Server-side pixel endpoint (GET /api/v1/attribution/pixel) is
    phase-H-pending; URL shape locked in now so email formatter can
    embed pixels today and backfill will work when the endpoint lands.
    """
    params = {"rick_id": rick_id}
    if msg_id:
        params["msg_id"] = msg_id
    return f"https://api.meetrick.ai/api/v1/attribution/pixel?{urllib.parse.urlencode(params)}"


if __name__ == "__main__":
    # Quick self-test when invoked directly.
    import sys
    print(build_install_url("reddit", "distribution", "abc123"))
    print(stamp_urls_in_text("Visit https://meetrick.ai for more.", "reddit", "distribution", "abc"))
    print(stamp_urls_in_text(
        "Already stamped: https://meetrick.ai/?utm_source=existing&foo=bar here.",
        "email", None, "xyz",
    ))
    sys.exit(0)
