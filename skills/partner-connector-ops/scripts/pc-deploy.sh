#!/usr/bin/env bash
# Partner Connector deployment helper.
#
# Usage:
#   pc-deploy.sh --status     # Current deployment status
#   pc-deploy.sh --logs       # Recent pod logs
#   pc-deploy.sh --rollback   # Show rollback commands

set -euo pipefail

ACTION="status"

while [[ $# -gt 0 ]]; do
    case $1 in
        --status) ACTION="status"; shift ;;
        --logs) ACTION="logs"; shift ;;
        --rollback) ACTION="rollback"; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "═══════════════════════════════════════"
echo "  Partner Connector — Deployment"
echo "  $(date '+%Y-%m-%d %H:%M')"
echo "═══════════════════════════════════════"
echo ""

case $ACTION in
    status)
        echo "📋 Deployment Status"
        echo "---"

        if command -v kubectl &> /dev/null; then
            echo "Production:"
            kubectl rollout status deployment/partner-connector-api -n partner-connector 2>/dev/null || echo "  ⚠️  Cannot reach cluster"
            echo ""
            echo "Staging:"
            kubectl rollout status deployment/partner-connector-api -n partner-connector-staging 2>/dev/null || echo "  ⚠️  Cannot reach cluster"
        else
            echo "  ⚠️  kubectl not available"
            echo ""
            echo "  Manual check:"
            echo "  curl -sf https://api.partners.belkins.io/health"
            echo "  curl -sf https://staging-api.partners.belkins.io/health"
        fi

        echo ""
        echo "🔄 Deployment Stack:"
        echo "  1. Push to main → GitHub Actions"
        echo "  2. Semantic release → version tag"
        echo "  3. Docker build → DigitalOcean Registry"
        echo "  4. ArgoCD manifest update → GitLab"
        echo "  5. ArgoCD sync → Kubernetes"
        echo "  6. Keel auto-update (polls every 2 min)"
        ;;

    logs)
        echo "📝 Recent Pod Logs"
        echo "---"

        if command -v kubectl &> /dev/null; then
            echo "Production (last 50 lines):"
            kubectl logs -l app=partner-connector-api -n partner-connector --tail=50 2>/dev/null || echo "  ⚠️  Cannot reach cluster"
        else
            echo "  ⚠️  kubectl not available"
        fi
        ;;

    rollback)
        echo "⏪ Rollback Commands"
        echo "---"
        echo ""
        echo "# View rollout history"
        echo "kubectl rollout history deployment/partner-connector-api -n partner-connector"
        echo ""
        echo "# Rollback to previous version"
        echo "kubectl rollout undo deployment/partner-connector-api -n partner-connector"
        echo ""
        echo "# Rollback to specific revision"
        echo "kubectl rollout undo deployment/partner-connector-api --to-revision=N -n partner-connector"
        echo ""
        echo "# Verify rollback"
        echo "kubectl rollout status deployment/partner-connector-api -n partner-connector"
        echo ""
        echo "⚠️  Always verify health after rollback:"
        echo "curl -sf https://api.partners.belkins.io/health"
        ;;
esac

echo ""
echo "═══════════════════════════════════════"
