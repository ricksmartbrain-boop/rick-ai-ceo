# Partner Connector Ops Skill

Product operations for Partner Connector — deployment monitoring, customer health, feature pipeline.

## Architecture Context
- **Stack:** NestJS 10.x, MongoDB, Redis/BullMQ, Kubernetes on DigitalOcean
- **Production:** https://api.partners.belkins.io (namespace: partner-connector)
- **Staging:** https://staging-api.partners.belkins.io (namespace: partner-connector-staging)
- **Repo:** `RICK_PARTNER_CONNECTOR_REPO`
- **Tests:** 563 passing, zero TypeScript errors

## Commands

### pc-health.sh
Extended health check — API, Redis queues, errors, pod status.
```bash
bash scripts/pc-health.sh              # Full health check
bash scripts/pc-health.sh --prod       # Production only
bash scripts/pc-health.sh --staging    # Staging only
bash scripts/pc-health.sh --queues     # Queue depths only
```

### pc-customers.sh
Customer health dashboard — active partners, leads, revenue indicators.
```bash
bash scripts/pc-customers.sh           # Full customer dashboard
```

### pc-deploy.sh
Deployment helper — staging → production flow.
```bash
bash scripts/pc-deploy.sh --status     # Current deployment status
bash scripts/pc-deploy.sh --logs       # Recent pod logs
```

## Revenue Target: $30,000/month
- Scale partner count from 10 to 25+
- Increase lead volume and quality
- Launch partner-to-partner trading (20% commission)

## BullMQ Queues
| Queue | Purpose |
|-------|---------|
| hubspot-webhook | HubSpot webhook events |
| chilipiper-webhook | ChiliPiper meetings |
| stripe-webhook | Payment events |
| partner-api-lead | Partner API updates |
| events | Audit logging |
| sync-leads | Bulk HubSpot sync |
| partner-sync | Bidirectional sync |
| reservation-expiry | Marketplace expiry |
