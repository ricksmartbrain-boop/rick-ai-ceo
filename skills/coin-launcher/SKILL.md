# Coin Launcher Skill

Token deployment and management on Base (Ethereum L2). Handles ERC-20 creation, liquidity pool setup, and token monitoring.

## CRITICAL: Token Launch Decision Framework

A token launch is IRREVERSIBLE and RISKY. Only proceed when ALL of the following conditions are met:

1. **The founder gives explicit written approval** (not implied, not "maybe")
2. **Community of 1,000+ engaged followers exists** (newsletter + social combined)
3. **Clear utility beyond speculation** (e.g., course access, community membership, governance)
4. **Legal review completed** (or token is clearly a meme/community token with no financial promises)
5. **Initial liquidity budget approved** ($1K-$5K set aside for DEX pool)

### When NOT to Launch

- "Just because Felix did it" is NOT a valid reason
- No existing community to distribute to
- No clear utility for token holders
- Before reaching $10K/month in product revenue
- If the only purpose is speculation or pump-and-dump
- If the founder hasn't explicitly said "yes, deploy the token"

### If All Conditions Met

Deployment flow:

```
1. Deploy ERC-20 on Base (via Foundry/forge)
2. Create liquidity pool on Uniswap V3 (Base)
3. Allocate tokens:
   - 50% Community (airdrops, rewards, staking)
   - 20% Treasury (development fund)
   - 20% Liquidity (DEX pool)
   - 10% Team (vested, 12-month cliff)
4. Announce via newsletter + social media
5. Set up Snapshot governance (community voting)
```

## Tech Stack

| Component | Technology |
|-----------|------------|
| Token standard | ERC-20 (OpenZeppelin) |
| Blockchain | Base (Ethereum L2) |
| Deployment | Foundry (forge) |
| DEX | Uniswap V3 on Base |
| Governance | Snapshot |
| Wallet | Via WALLET_PRIVATE_KEY env var (NEVER hardcoded) |

## Triggers

- **Token launch:** Only when all 5 conditions above are met
- **Token status:** "How's the token doing?", "Check token stats"
- **Market check:** Monitor price, holders, liquidity

## Commands

### token-deploy.sh

Deploy an ERC-20 token on Base.

```bash
# Dry run (simulate, no actual deployment)
bash scripts/token-deploy.sh --name "RickCoin" --symbol "RICK" --supply 1000000000 --network base-sepolia --dry-run

# Deploy to testnet
bash scripts/token-deploy.sh --name "RickCoin" --symbol "RICK" --supply 1000000000 --network base-sepolia

# Deploy to mainnet (requires VLAD_APPROVAL=true)
VLAD_APPROVAL=true bash scripts/token-deploy.sh --name "RickCoin" --symbol "RICK" --supply 1000000000 --network base
```

### token-status.sh

Monitor deployed token.

```bash
# Token info and holder count
bash scripts/token-status.sh --address 0x1234...abcd --holders

# Current DEX price
bash scripts/token-status.sh --address 0x1234...abcd --price

# Liquidity pool depth
bash scripts/token-status.sh --address 0x1234...abcd --liquidity

# Treasury wallet balance
bash scripts/token-status.sh --address 0x1234...abcd --treasury
```

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `WALLET_PRIVATE_KEY` | Yes (deploy) | Deployer wallet key (NEVER hardcode) |
| `RPC_URL` | Yes | Base RPC endpoint |
| `BASESCAN_API_KEY` | Yes (status) | Block explorer API for holder/price queries |
| `VLAD_APPROVAL` | Yes (mainnet) | Must be `true` for mainnet deployment |

## Token Allocation

| Allocation | Percentage | Purpose |
|------------|------------|---------|
| Community | 50% | Airdrops, rewards, staking |
| Treasury | 20% | Development fund |
| Liquidity | 20% | DEX pool (Uniswap V3) |
| Team | 10% | Vested, 12-month cliff |

## Safety Checklist

Before ANY mainnet deployment, verify:

- [ ] VLAD_APPROVAL=true is set
- [ ] Community size > 1,000 followers
- [ ] Token utility is defined and documented
- [ ] Legal review is complete (or clearly meme token)
- [ ] Liquidity budget ($1K-$5K) is approved and funded
- [ ] Testnet deployment succeeded without errors
- [ ] Token allocation percentages are correct
- [ ] Treasury wallet address is correct
- [ ] Contract source code is verified on Basescan
