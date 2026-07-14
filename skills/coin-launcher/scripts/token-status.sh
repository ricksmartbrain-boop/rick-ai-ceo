#!/usr/bin/env bash
# Token status — monitor deployed token on Base.
#
# Usage:
#   token-status.sh --address <contract> --holders
#   token-status.sh --address <contract> --price
#   token-status.sh --address <contract> --liquidity
#   token-status.sh --address <contract> --treasury
#
# Environment:
#   BASESCAN_API_KEY  Required. Block explorer API key.

set -euo pipefail

# ─── Config ──────────────────────────────────────────────────────────
BASESCAN_KEY="${BASESCAN_API_KEY:-}"
BASESCAN_URL="https://api.basescan.org/api"

CONTRACT=""
COMMAND=""

usage() {
    echo "Usage:"
    echo "  token-status.sh --address <contract> --holders"
    echo "  token-status.sh --address <contract> --price"
    echo "  token-status.sh --address <contract> --liquidity"
    echo "  token-status.sh --address <contract> --treasury"
    echo ""
    echo "Options:"
    echo "  --address    Token contract address"
    echo "  --holders    Show holder count"
    echo "  --price      Show current DEX price"
    echo "  --liquidity  Show liquidity pool depth"
    echo "  --treasury   Show treasury wallet balance"
    echo ""
    echo "Environment:"
    echo "  BASESCAN_API_KEY  Required. Block explorer API key."
    exit 1
}

check_api_key() {
    if [ -z "$BASESCAN_KEY" ]; then
        echo "Error: BASESCAN_API_KEY is not set."
        echo "Get one at https://basescan.org/apis"
        exit 1
    fi
}

basescan_api() {
    local params="$1"
    curl -s "$BASESCAN_URL?$params&apikey=$BASESCAN_KEY"
}

# ─── Parse Args ──────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case $1 in
        --address) CONTRACT="$2"; shift 2 ;;
        --holders) COMMAND="holders"; shift ;;
        --price) COMMAND="price"; shift ;;
        --liquidity) COMMAND="liquidity"; shift ;;
        --treasury) COMMAND="treasury"; shift ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

if [ -z "$CONTRACT" ] || [ -z "$COMMAND" ]; then
    echo "Error: --address and a command (--holders, --price, --liquidity, --treasury) are required."
    usage
fi

check_api_key

# ─── Commands ────────────────────────────────────────────────────────

cmd_holders() {
    echo "================================================"
    echo "  Token Holders: $CONTRACT"
    echo "  $(date '+%Y-%m-%d %H:%M')"
    echo "================================================"
    echo ""

    # Get token info
    local token_info
    token_info=$(basescan_api "module=token&action=tokeninfo&contractaddress=$CONTRACT")

    echo "$token_info" | python3 -c "
import sys, json

data = json.load(sys.stdin)
result = data.get('result', [])

if isinstance(result, list) and len(result) > 0:
    info = result[0]
    print(f\"  Name:         {info.get('tokenName', 'N/A')}\")
    print(f\"  Symbol:       {info.get('symbol', 'N/A')}\")
    print(f\"  Total Supply: {info.get('totalSupply', 'N/A')}\")
    print(f\"  Decimals:     {info.get('divisor', 'N/A')}\")
    print(f\"  Holders:      {info.get('holdersCount', 'N/A')}\")
elif isinstance(result, str):
    print(f'  Info: {result}')
else:
    print('  Could not fetch token info.')
" 2>/dev/null

    # Get transfer count as proxy for activity
    local transfers
    transfers=$(basescan_api "module=account&action=tokentx&contractaddress=$CONTRACT&page=1&offset=1&sort=desc")

    echo ""
    echo "$transfers" | python3 -c "
import sys, json

data = json.load(sys.stdin)
result = data.get('result', [])

if isinstance(result, list) and len(result) > 0:
    last_tx = result[0]
    from datetime import datetime
    ts = int(last_tx.get('timeStamp', 0))
    if ts:
        dt = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')
        print(f'  Last Transfer: {dt}')
    print(f\"  From: {last_tx.get('from', 'N/A')[:10]}...{last_tx.get('from', 'N/A')[-6:]}\")
    print(f\"  To:   {last_tx.get('to', 'N/A')[:10]}...{last_tx.get('to', 'N/A')[-6:]}\")
else:
    print('  No transfers found.')
" 2>/dev/null

    echo ""
    echo "  Basescan: https://basescan.org/token/$CONTRACT"
    echo ""
    echo "================================================"
}

cmd_price() {
    echo "================================================"
    echo "  Token Price: $CONTRACT"
    echo "  $(date '+%Y-%m-%d %H:%M')"
    echo "================================================"
    echo ""

    # Try to get price from DEXScreener API (free, no key needed)
    local dex_response
    dex_response=$(curl -s "https://api.dexscreener.com/latest/dex/tokens/$CONTRACT" 2>/dev/null)

    echo "$dex_response" | python3 -c "
import sys, json

data = json.load(sys.stdin)
pairs = data.get('pairs', [])

if not pairs:
    print('  No trading pairs found on DEXes.')
    print('  Token may not have a liquidity pool yet.')
    sys.exit(0)

# Show the most liquid pair
pair = pairs[0]
print(f\"  DEX:        {pair.get('dexId', 'N/A')}\")
print(f\"  Pair:       {pair.get('baseToken', {}).get('symbol', '?')}/{pair.get('quoteToken', {}).get('symbol', '?')}\")
print(f\"  Price USD:  \${pair.get('priceUsd', 'N/A')}\")
print(f\"  Price Native: {pair.get('priceNative', 'N/A')}\")
print(f\"  24h Volume: \${float(pair.get('volume', {}).get('h24', 0)):,.2f}\")
print(f\"  24h Change: {pair.get('priceChange', {}).get('h24', 'N/A')}%\")
print(f\"  Liquidity:  \${float(pair.get('liquidity', {}).get('usd', 0)):,.2f}\")
print(f\"  FDV:        \${float(pair.get('fdv', 0)):,.2f}\")

if len(pairs) > 1:
    print(f'')
    print(f'  ({len(pairs)} trading pairs found, showing most liquid)')
" 2>/dev/null

    echo ""
    echo "  DexScreener: https://dexscreener.com/base/$CONTRACT"
    echo ""
    echo "================================================"
}

cmd_liquidity() {
    echo "================================================"
    echo "  Liquidity Pool: $CONTRACT"
    echo "  $(date '+%Y-%m-%d %H:%M')"
    echo "================================================"
    echo ""

    # Use DexScreener for liquidity info
    local dex_response
    dex_response=$(curl -s "https://api.dexscreener.com/latest/dex/tokens/$CONTRACT" 2>/dev/null)

    echo "$dex_response" | python3 -c "
import sys, json

data = json.load(sys.stdin)
pairs = data.get('pairs', [])

if not pairs:
    print('  No liquidity pools found.')
    print('  Create one on Uniswap V3 (Base).')
    sys.exit(0)

print(f'  Found {len(pairs)} pool(s)')
print('')

for i, pair in enumerate(pairs):
    print(f\"  Pool {i+1}: {pair.get('baseToken', {}).get('symbol', '?')}/{pair.get('quoteToken', {}).get('symbol', '?')}\")
    print(f\"    DEX:       {pair.get('dexId', 'N/A')}\")
    print(f\"    Liquidity: \${float(pair.get('liquidity', {}).get('usd', 0)):,.2f}\")
    print(f\"    Volume 24h: \${float(pair.get('volume', {}).get('h24', 0)):,.2f}\")
    print(f\"    Txns 24h:  {pair.get('txns', {}).get('h24', {}).get('buys', 0)} buys / {pair.get('txns', {}).get('h24', {}).get('sells', 0)} sells\")
    print(f\"    Pool:      {pair.get('pairAddress', 'N/A')}\")
    print('')
" 2>/dev/null

    echo "================================================"
}

cmd_treasury() {
    echo "================================================"
    echo "  Treasury Balance: $CONTRACT"
    echo "  $(date '+%Y-%m-%d %H:%M')"
    echo "================================================"
    echo ""

    # Get ETH balance of the contract
    local balance
    balance=$(basescan_api "module=account&action=balance&address=$CONTRACT&tag=latest")

    echo "$balance" | python3 -c "
import sys, json

data = json.load(sys.stdin)
result = data.get('result', '0')

if result and result != '0':
    wei = int(result)
    eth = wei / 1e18
    print(f'  ETH Balance: {eth:.6f} ETH')
else:
    print('  ETH Balance: 0 ETH')
" 2>/dev/null

    # Get token balance of the contract itself (treasury held in contract)
    local token_balance
    token_balance=$(basescan_api "module=account&action=tokenbalance&contractaddress=$CONTRACT&address=$CONTRACT&tag=latest")

    echo "$token_balance" | python3 -c "
import sys, json

data = json.load(sys.stdin)
result = data.get('result', '0')

if result and result != '0':
    balance = int(result)
    # Assume 18 decimals
    tokens = balance / 1e18
    print(f'  Token Balance (in contract): {tokens:,.0f} tokens')
else:
    print('  Token Balance (in contract): 0 tokens')
" 2>/dev/null

    echo ""
    echo "  Note: Treasury may be held in a separate wallet."
    echo "  Use --address <treasury_wallet> to check a specific wallet."
    echo ""
    echo "  Basescan: https://basescan.org/address/$CONTRACT"
    echo ""
    echo "================================================"
}

# ─── Main ────────────────────────────────────────────────────────────

case "$COMMAND" in
    holders)   cmd_holders ;;
    price)     cmd_price ;;
    liquidity) cmd_liquidity ;;
    treasury)  cmd_treasury ;;
    *)
        echo "Error: Unknown command '$COMMAND'"
        usage
        ;;
esac
