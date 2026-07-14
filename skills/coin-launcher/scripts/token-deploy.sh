#!/usr/bin/env bash
# Token deployment — deploy ERC-20 on Base via Foundry.
#
# Usage:
#   token-deploy.sh --name <name> --symbol <symbol> --supply <amount> --network base|base-sepolia [--dry-run]
#
# Environment:
#   WALLET_PRIVATE_KEY  Required. Deployer wallet private key.
#   RPC_URL             Required. Base RPC endpoint.
#   VLAD_APPROVAL       Required for mainnet. Must be "true".

set -euo pipefail

# ─── Config ──────────────────────────────────────────────────────────
WALLET_KEY="${WALLET_PRIVATE_KEY:-}"
RPC="${RPC_URL:-}"
APPROVAL="${VLAD_APPROVAL:-false}"

TOKEN_NAME=""
TOKEN_SYMBOL=""
TOKEN_SUPPLY=""
NETWORK="base-sepolia"
DRY_RUN=false

usage() {
    echo "Usage:"
    echo "  token-deploy.sh --name <name> --symbol <symbol> --supply <amount> --network base|base-sepolia [--dry-run]"
    echo ""
    echo "Options:"
    echo "  --name       Token name (e.g., 'RickCoin')"
    echo "  --symbol     Ticker symbol (e.g., 'RICK')"
    echo "  --supply     Initial supply (e.g., 1000000000)"
    echo "  --network    Target network: 'base' (mainnet) or 'base-sepolia' (testnet)"
    echo "  --dry-run    Simulate deployment without executing"
    echo ""
    echo "Environment:"
    echo "  WALLET_PRIVATE_KEY  Required. Deployer wallet key."
    echo "  RPC_URL             Required. Base RPC endpoint."
    echo "  VLAD_APPROVAL       Required for mainnet. Must be 'true'."
    exit 1
}

# ─── Parse Args ──────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case $1 in
        --name) TOKEN_NAME="$2"; shift 2 ;;
        --symbol) TOKEN_SYMBOL="$2"; shift 2 ;;
        --supply) TOKEN_SUPPLY="$2"; shift 2 ;;
        --network) NETWORK="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

# ─── Validation ──────────────────────────────────────────────────────

if [ -z "$TOKEN_NAME" ] || [ -z "$TOKEN_SYMBOL" ] || [ -z "$TOKEN_SUPPLY" ]; then
    echo "Error: --name, --symbol, and --supply are required."
    usage
fi

if [ "$NETWORK" != "base" ] && [ "$NETWORK" != "base-sepolia" ]; then
    echo "Error: --network must be 'base' or 'base-sepolia'."
    exit 1
fi

if [ -z "$WALLET_KEY" ]; then
    echo "Error: WALLET_PRIVATE_KEY is not set."
    echo "Export it as an environment variable. NEVER hardcode it."
    exit 1
fi

if [ -z "$RPC" ]; then
    echo "Error: RPC_URL is not set."
    echo "Export it as an environment variable."
    exit 1
fi

# ─── Safety Gates ────────────────────────────────────────────────────

echo ""
echo "============================================================"
echo ""
echo "  TOKEN DEPLOYMENT"
echo ""
echo "  Name:    $TOKEN_NAME"
echo "  Symbol:  $TOKEN_SYMBOL"
echo "  Supply:  $TOKEN_SUPPLY"
echo "  Network: $NETWORK"
echo "  Dry Run: $DRY_RUN"
echo ""
echo "============================================================"
echo ""

# Mainnet safety gate
if [ "$NETWORK" = "base" ]; then
    if [ "$APPROVAL" != "true" ]; then
        echo "============================================================"
        echo ""
        echo "  MAINNET DEPLOYMENT BLOCKED"
        echo ""
        echo "  VLAD_APPROVAL is not set to 'true'."
        echo "  Mainnet deployment requires explicit approval."
        echo ""
        echo "  Set: export VLAD_APPROVAL=true"
        echo ""
        echo "  Before approving, verify ALL conditions:"
        echo "  1. Founder gave explicit written approval"
        echo "  2. Community of 1,000+ engaged followers"
        echo "  3. Clear utility beyond speculation"
        echo "  4. Legal review completed"
        echo "  5. Liquidity budget approved (\$1K-\$5K)"
        echo ""
        echo "============================================================"
        exit 1
    fi

    echo ""
    echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    echo "!!                                                       !!"
    echo "!!   WARNING: TOKEN DEPLOYMENT IS IRREVERSIBLE           !!"
    echo "!!                                                       !!"
    echo "!!   You are about to deploy a token to BASE MAINNET.    !!"
    echo "!!   This action CANNOT be undone.                       !!"
    echo "!!   Real money (gas fees) will be spent.                !!"
    echo "!!                                                       !!"
    echo "!!   Token: $TOKEN_NAME ($TOKEN_SYMBOL)"
    echo "!!   Supply: $TOKEN_SUPPLY"
    echo "!!   Network: Base Mainnet"
    echo "!!                                                       !!"
    echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    echo ""
    echo "Type 'I CONFIRM' to proceed:"
    read -r confirmation

    if [ "$confirmation" != "I CONFIRM" ]; then
        echo ""
        echo "Deployment cancelled. You typed: '$confirmation'"
        echo "Expected: 'I CONFIRM'"
        exit 1
    fi

    echo ""
    echo "Confirmation received. Proceeding with mainnet deployment..."
    echo ""
fi

# ─── Dry Run ─────────────────────────────────────────────────────────

if [ "$DRY_RUN" = true ]; then
    echo "DRY RUN MODE — No actual deployment."
    echo ""
    echo "Would deploy:"
    echo "  Contract: ERC-20 (OpenZeppelin)"
    echo "  Name:     $TOKEN_NAME"
    echo "  Symbol:   $TOKEN_SYMBOL"
    echo "  Supply:   $TOKEN_SUPPLY"
    echo "  Network:  $NETWORK"
    echo "  RPC:      $RPC"
    echo ""
    echo "Token Allocation:"
    echo "  50% Community ($(echo "$TOKEN_SUPPLY * 50 / 100" | bc) tokens)"
    echo "  20% Treasury  ($(echo "$TOKEN_SUPPLY * 20 / 100" | bc) tokens)"
    echo "  20% Liquidity ($(echo "$TOKEN_SUPPLY * 20 / 100" | bc) tokens)"
    echo "  10% Team      ($(echo "$TOKEN_SUPPLY * 10 / 100" | bc) tokens)"
    echo ""
    echo "Next steps (if proceeding):"
    echo "  1. Remove --dry-run flag"
    echo "  2. Ensure forge (Foundry) is installed"
    echo "  3. Run deployment"
    echo "  4. Verify contract on Basescan"
    echo "  5. Create liquidity pool on Uniswap V3"
    echo ""
    echo "Dry run complete."
    exit 0
fi

# ─── Deploy ──────────────────────────────────────────────────────────

# Check for forge (Foundry)
if ! command -v forge &> /dev/null; then
    echo "Error: 'forge' (Foundry) is not installed."
    echo "Install: curl -L https://foundry.paradigm.xyz | bash && foundryup"
    exit 1
fi

echo "Deploying $TOKEN_NAME ($TOKEN_SYMBOL) to $NETWORK..."
echo ""

# Create temporary Foundry project for deployment
DEPLOY_DIR=$(mktemp -d)
trap 'rm -rf "$DEPLOY_DIR"' EXIT

cd "$DEPLOY_DIR"

# Initialize Foundry project
forge init --no-commit --quiet .

# Install OpenZeppelin
forge install OpenZeppelin/openzeppelin-contracts --no-commit --quiet

# Create the token contract
cat > src/Token.sol << SOLIDITY
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "openzeppelin-contracts/contracts/token/ERC20/ERC20.sol";

contract Token is ERC20 {
    constructor() ERC20("$TOKEN_NAME", "$TOKEN_SYMBOL") {
        _mint(msg.sender, $TOKEN_SUPPLY * 10 ** decimals());
    }
}
SOLIDITY

# Add remappings
echo "openzeppelin-contracts/=lib/openzeppelin-contracts/" > remappings.txt

# Build
echo "Building contract..."
forge build --quiet

# Deploy
echo "Deploying to $NETWORK..."
DEPLOY_OUTPUT=$(forge create src/Token.sol:Token \
    --rpc-url "$RPC" \
    --private-key "$WALLET_KEY" \
    2>&1)

echo "$DEPLOY_OUTPUT"

# Extract contract address
CONTRACT_ADDRESS=$(echo "$DEPLOY_OUTPUT" | grep -oP 'Deployed to: \K0x[a-fA-F0-9]+' || echo "")

if [ -z "$CONTRACT_ADDRESS" ]; then
    echo ""
    echo "Error: Could not extract contract address from deployment output."
    echo "Check the output above for errors."
    exit 1
fi

echo ""
echo "============================================================"
echo ""
echo "  DEPLOYMENT SUCCESSFUL"
echo ""
echo "  Contract: $CONTRACT_ADDRESS"
echo "  Network:  $NETWORK"
echo "  Token:    $TOKEN_NAME ($TOKEN_SYMBOL)"
echo "  Supply:   $TOKEN_SUPPLY"
echo ""
echo "  Next Steps:"
echo "  1. Verify contract: forge verify-contract $CONTRACT_ADDRESS src/Token.sol:Token --chain $NETWORK"
echo "  2. Check status: token-status.sh --address $CONTRACT_ADDRESS --holders"
echo "  3. Create Uniswap V3 liquidity pool"
echo "  4. Announce via newsletter + social"
echo ""
echo "============================================================"
