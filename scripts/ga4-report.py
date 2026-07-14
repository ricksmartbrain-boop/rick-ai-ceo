#!/usr/bin/env python3
"""
GA4 Report Tool for meetrick.ai
Uses Google Analytics Data API v1beta with service account auth.

Usage:
  python3 ga4-report.py                    # default: last 7 days overview
  python3 ga4-report.py --days 30          # last 30 days
  python3 ga4-report.py --today            # today only
  python3 ga4-report.py --report realtime  # real-time active users
  python3 ga4-report.py --report pages     # top pages
  python3 ga4-report.py --report sources   # traffic sources
  python3 ga4-report.py --report all       # everything
  python3 ga4-report.py --json             # output raw JSON

Setup:
  1. Create Google service account at https://console.cloud.google.com/iam-admin/serviceaccounts
  2. Download JSON key → save at ~/.config/google/ga4-service-account.json
  3. Enable Google Analytics Data API in the project
  4. Share GA4 property with the service account email (Viewer role)
     - GA4 Admin → Property → Property Access Management → Add users
     - Service account email from the JSON key file
"""

import os, sys, json, argparse, warnings
from datetime import datetime, timedelta
from pathlib import Path

warnings.filterwarnings("ignore")

# ── Config ───────────────────────────────────────────────────────────────────
GA4_PROPERTY_ID = os.environ.get("GA4_PROPERTY_ID", "properties/YOUR_PROPERTY_ID")
SERVICE_ACCOUNT_PATH = os.environ.get(
    "GOOGLE_APPLICATION_CREDENTIALS",
    str(Path.home() / ".config/google/ga4-service-account.json")
)
# Measurement ID from MEMORY.md: G-G8VNRGNMLH
# Property ID is different — find it at analytics.google.com → Admin → Property Settings

def check_setup():
    """Verify credentials and property ID are configured."""
    errors = []
    if not Path(SERVICE_ACCOUNT_PATH).exists():
        errors.append(f"Service account JSON not found at: {SERVICE_ACCOUNT_PATH}")
    if "YOUR_PROPERTY_ID" in GA4_PROPERTY_ID:
        errors.append(
            "GA4_PROPERTY_ID not set. Find it at:\n"
            "  analytics.google.com → Admin → Property Settings → Property ID\n"
            "  Then set: export GA4_PROPERTY_ID=properties/XXXXXXXXX\n"
            "  Or add GA4_PROPERTY_ID to ~/clawd/config/rick.env"
        )
    if errors:
        print("⚠️  GA4 setup incomplete:\n")
        for e in errors:
            print(f"  ✗ {e}\n")
        print("📋 Full setup guide:")
        print("  1. Go to https://console.cloud.google.com/apis/library/analyticsdata.googleapis.com")
        print("     → Enable 'Google Analytics Data API'")
        print("  2. Go to https://console.cloud.google.com/iam-admin/serviceaccounts")
        print("     → Create service account → Download JSON key")
        print(f"     → Save to: {SERVICE_ACCOUNT_PATH}")
        print("  3. Go to analytics.google.com → Admin → Property Access Management")
        print("     → Add the service account email as Viewer")
        print("  4. Get Property ID from Admin → Property Settings")
        print("     → Add to rick.env: GA4_PROPERTY_ID=properties/XXXXXXXXX")
        return False
    return True


def get_client():
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_PATH,
        scopes=["https://www.googleapis.com/auth/analytics.readonly"]
    )
    return BetaAnalyticsDataClient(credentials=creds)


def run_report(client, dimensions, metrics, date_ranges, order_bys=None, limit=10):
    from google.analytics.data_v1beta.types import (
        RunReportRequest, Dimension, Metric, DateRange, OrderBy
    )
    request = RunReportRequest(
        property=GA4_PROPERTY_ID,
        dimensions=[Dimension(name=d) for d in dimensions],
        metrics=[Metric(name=m) for m in metrics],
        date_ranges=date_ranges,
        limit=limit,
    )
    if order_bys:
        request.order_bys = order_bys
    return client.run_report(request)


def run_realtime(client):
    from google.analytics.data_v1beta.types import RunRealtimeReportRequest, Dimension, Metric
    request = RunRealtimeReportRequest(
        property=GA4_PROPERTY_ID,
        dimensions=[Dimension(name="country"), Dimension(name="unifiedScreenName")],
        metrics=[Metric(name="activeUsers")],
    )
    return client.run_realtime_report(request)


def format_response(response, title):
    lines = [f"\n{'═'*50}", f"  {title}", f"{'═'*50}"]
    headers = [d.name for d in response.dimension_headers] + \
              [m.name for m in response.metric_headers]
    col_widths = [max(len(h), 20) for h in headers]

    header_row = "  " + "  ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    lines.append(header_row)
    lines.append("  " + "-" * (sum(col_widths) + 2 * len(col_widths)))

    for row in response.rows:
        vals = [d.value for d in row.dimension_values] + \
               [m.value for m in row.metric_values]
        lines.append("  " + "  ".join(str(v).ljust(col_widths[i]) for i, v in enumerate(vals)))

    total_rows = response.row_count
    lines.append(f"\n  Total rows: {total_rows}")
    return "\n".join(lines)


def overview_report(client, days=7, as_json=False):
    from google.analytics.data_v1beta.types import DateRange, OrderBy

    date_range = [DateRange(start_date=f"{days}daysAgo", end_date="today")]

    # Sessions + users by day
    daily = run_report(
        client,
        dimensions=["date"],
        metrics=["sessions", "activeUsers", "newUsers", "bounceRate", "averageSessionDuration"],
        date_ranges=date_range,
        limit=days + 1
    )

    # Top pages
    pages = run_report(
        client,
        dimensions=["pagePath"],
        metrics=["screenPageViews", "activeUsers", "averageSessionDuration"],
        date_ranges=date_range,
        limit=10
    )

    # Traffic sources
    sources = run_report(
        client,
        dimensions=["sessionSource", "sessionMedium"],
        metrics=["sessions", "activeUsers", "newUsers"],
        date_ranges=date_range,
        limit=10
    )

    # Countries
    geo = run_report(
        client,
        dimensions=["country"],
        metrics=["sessions", "activeUsers"],
        date_ranges=date_range,
        limit=10
    )

    # Devices
    devices = run_report(
        client,
        dimensions=["deviceCategory"],
        metrics=["sessions", "activeUsers"],
        date_ranges=date_range,
        limit=5
    )

    if as_json:
        def to_dict(r):
            headers = [d.name for d in r.dimension_headers] + [m.name for m in r.metric_headers]
            rows = []
            for row in r.rows:
                vals = [d.value for d in row.dimension_values] + [m.value for m in row.metric_values]
                rows.append(dict(zip(headers, vals)))
            return {"headers": headers, "rows": rows, "total": r.row_count}
        print(json.dumps({
            "property": GA4_PROPERTY_ID,
            "period_days": days,
            "daily": to_dict(daily),
            "pages": to_dict(pages),
            "sources": to_dict(sources),
            "geo": to_dict(geo),
            "devices": to_dict(devices),
        }, indent=2))
        return

    print(f"\n🤖 meetrick.ai — GA4 Report (last {days} days)")
    print(f"   Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')} PST")
    print(format_response(daily, f"Daily Traffic (last {days} days)"))
    print(format_response(pages, "Top Pages"))
    print(format_response(sources, "Traffic Sources"))
    print(format_response(geo, "Top Countries"))
    print(format_response(devices, "Device Breakdown"))


def realtime_report(client):
    response = run_realtime(client)
    total = sum(int(r.metric_values[0].value) for r in response.rows) if response.rows else 0
    print(f"\n🔴 meetrick.ai — Real-time Active Users: {total}")
    if response.rows:
        print(format_response(response, "Active Users by Country / Page"))
    else:
        print("  No active users right now.")


def main():
    parser = argparse.ArgumentParser(description="GA4 report for meetrick.ai")
    parser.add_argument("--days", type=int, default=7, help="Days to look back (default 7)")
    parser.add_argument("--today", action="store_true", help="Today only")
    parser.add_argument("--report", choices=["overview", "realtime", "pages", "sources", "all"],
                        default="overview")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    if not check_setup():
        sys.exit(1)

    client = get_client()

    days = 1 if args.today else args.days
    if args.report in ("overview", "all", "pages", "sources"):
        overview_report(client, days=days, as_json=args.json)
    if args.report in ("realtime", "all"):
        realtime_report(client)


if __name__ == "__main__":
    main()
