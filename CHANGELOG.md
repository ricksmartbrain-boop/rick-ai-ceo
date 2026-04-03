# Changelog

## v1.1.0 (2026-03-31)

### Product Sync with Rick v6
- 3 tier bundles upgraded: Free (5 skills), Pro (16 skills), Business (24 skills)
- New skills: talking-head (video avatars), instagram-slides (carousels), blog-image-generator, coding-agent-loops
- SOUL.md with autonomy principles (Boundaries + Growth sections)
- MEMORY-WARM.md warm memory layer for all tiers
- First Heartbeat behavior in all tiers
- Per-tier config files: health.json, token-budgets.json, lane-policy.json, approval-policy.json

### Infrastructure
- API deployed at api.meetrick.ai (Railway + Postgres)
- DNS: api.meetrick.ai + releases.meetrick.ai (Cloudflare redirect to GitHub Releases)
- Sentry error tracking on API + daemon
- Install script v1.1.0 with vault scaffolding, IDENTITY stamping, --tier business alias

### Rick Network
- 4 new database tables: network_reports, network_insights, network_recommendations, network_announcements
- Tier-gated network feed: free=stats, pro=+benchmarks+recommendations, business=+all
- Nightly aggregation: skill popularity, error trends, churn detection
- Admin announcements via /network/announce

### Fixes
- Tier sync: register preserves Stripe-assigned tier on re-registration
- repair.sh: lightweight re-registration without touching workspace
- Email capture gates on /hire-rick and /playbook pages
- Resend audience IDs unified
- Map page wired to canonical api.meetrick.ai domain
- Timing-safe secret comparisons (crypto.timingSafeEqual)

## v1.0.0 (2026-03-20)
- Initial release
- Install script with 3 tier bundles
- API backend with 8 endpoints
- Rick Map with live installations
- Auto-update system (weekly LaunchAgent/cron)
