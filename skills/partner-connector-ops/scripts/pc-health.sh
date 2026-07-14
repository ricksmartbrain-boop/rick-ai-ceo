#!/usr/bin/env bash
# Partner Connector extended health check.
#
# Usage:
#   pc-health.sh              # Full health check
#   pc-health.sh --prod       # Production only
#   pc-health.sh --staging    # Staging only
#   pc-health.sh --queues     # Queue depths only

set -euo pipefail

CHECK_PROD=true
CHECK_STAGING=true
CHECK_QUEUES=true

while [[ $# -gt 0 ]]; do
    case $1 in
        --prod) CHECK_STAGING=false; shift ;;
        --staging) CHECK_PROD=false; shift ;;
        --queues) CHECK_PROD=false; CHECK_STAGING=false; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "═══════════════════════════════════════"
echo "  Partner Connector — Health Check"
echo "  $(date '+%Y-%m-%d %H:%M')"
echo "═══════════════════════════════════════"
echo ""

# Production API
if $CHECK_PROD; then
    echo "🔵 Production API"
    echo "---"
    if curl -sf https://api.partners.belkins.io/health -o /dev/null -w "  HTTP %{http_code} (%{time_total}s)\n" 2>/dev/null; then
        echo "  ✅ Production API is healthy"
    else
        echo "  ❌ Production API is DOWN"
    fi
    echo ""
fi

# Staging API
if $CHECK_STAGING; then
    echo "🟡 Staging API"
    echo "---"
    if curl -sf https://staging-api.partners.belkins.io/health -o /dev/null -w "  HTTP %{http_code} (%{time_total}s)\n" 2>/dev/null; then
        echo "  ✅ Staging API is healthy"
    else
        echo "  ❌ Staging API is DOWN"
    fi
    echo ""
fi

# Redis / BullMQ Queues
if $CHECK_QUEUES; then
    echo "📊 BullMQ Queue Depths"
    echo "---"

    QUEUES=(
        "hubspot-webhook"
        "chilipiper-webhook"
        "stripe-webhook"
        "partner-api-lead"
        "events"
        "sync-leads"
        "partner-sync"
        "reservation-expiry"
    )

    REDIS_CMD="redis-cli"
    if [ -n "${REDIS_PASSWORD:-}" ]; then
        REDIS_CMD="redis-cli -a $REDIS_PASSWORD"
    fi

    for queue in "${QUEUES[@]}"; do
        waiting=$($REDIS_CMD LLEN "bull:$queue:wait" 2>/dev/null || echo "?")
        active=$($REDIS_CMD LLEN "bull:$queue:active" 2>/dev/null || echo "?")
        failed=$($REDIS_CMD ZCARD "bull:$queue:failed" 2>/dev/null || echo "?")

        status="✅"
        if [ "$waiting" != "?" ] && [ "$waiting" -gt 50 ] 2>/dev/null; then
            status="⚠️"
        fi
        if [ "$failed" != "?" ] && [ "$failed" -gt 0 ] 2>/dev/null; then
            status="🔴"
        fi

        printf "  %s %-25s wait:%s active:%s failed:%s\n" "$status" "$queue" "$waiting" "$active" "$failed"
    done
    echo ""
fi

# Kubernetes pod status (if kubectl available)
if command -v kubectl &> /dev/null; then
    echo "☸️  Kubernetes Pods"
    echo "---"

    if $CHECK_PROD; then
        echo "  Production (partner-connector):"
        kubectl get pods -n partner-connector --no-headers 2>/dev/null | sed 's/^/    /' || echo "    ⚠️  Cannot reach cluster"
    fi

    if $CHECK_STAGING; then
        echo "  Staging (partner-connector-staging):"
        kubectl get pods -n partner-connector-staging --no-headers 2>/dev/null | sed 's/^/    /' || echo "    ⚠️  Cannot reach cluster"
    fi
    echo ""
else
    echo "☸️  kubectl not available — skipping pod status"
    echo ""
fi

echo "═══════════════════════════════════════"
echo "  Health check complete"
echo "═══════════════════════════════════════"
