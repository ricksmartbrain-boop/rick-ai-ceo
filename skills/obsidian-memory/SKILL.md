# Obsidian Memory Skill

Manages Rick's persistent brain at ~/rick-vault/. Handles vault initialization, note writing, and querying.

## Vault Location
- **Rick's vault:** ~/rick-vault/ (PARA structure)
- **NOT the founder's general vault** -- Rick writes to ~/rick-vault/ exclusively

## Commands

### init-rick-workspace.sh
Initialize or verify the ~/rick-vault/ vault structure.
```bash
bash scripts/init-rick-workspace.sh
```

### write-note.sh
Write an atomic note with proper frontmatter.
```bash
bash scripts/write-note.sh --type revenue --path "revenue/2026-03-05.md" --title "Revenue Snapshot" --content "..."
bash scripts/write-note.sh --type decision --path "decisions/2026-03-05-pricing.md" --title "Pricing Decision" --content "..."
bash scripts/write-note.sh --type daily --path "memory/2026-03-05.md" --title "Daily Notes" --content "..."
```

### query-vault.sh
Search the vault by type, tag, or date range.
- **Default (`--search`):** substring match via rebuild-memory-index.py — fast, exact
- **Ranked (`--search --ranked`):** BM25-scored via memory-search.py — better for multi-word/fuzzy queries at scale
```bash
bash scripts/query-vault.sh --type project
bash scripts/query-vault.sh --type revenue --after 2026-03-01
bash scripts/query-vault.sh --search "partner connector"
bash scripts/query-vault.sh --search "partner connector" --ranked
bash scripts/query-vault.sh --tier hot --project partner-connector
```

### rebuild-memory-index.py
Rebuild the hot/warm/cold memory index and overview dashboard.
```bash
python3 scripts/rebuild-memory-index.py rebuild --write
python3 scripts/rebuild-memory-index.py query --search "launch" --tier hot
```

### memory-search.py (BM25 ranked search)
Ranked memory retrieval using BM25 scoring with recency and tier boosts.
Replaces simple substring matching for scale (hundreds of notes).
```bash
python3 scripts/memory-search.py search "revenue stripe target"
python3 scripts/memory-search.py search "customer support" --tier hot --limit 5
python3 scripts/memory-search.py search "launch playbook" --json
python3 scripts/memory-search.py stats
```

## Conventions
- Always include `type` in frontmatter
- Write atomic notes -- one idea per note
- Link bidirectionally between related notes using [[wikilinks]]
- Never delete -- set status to archived or superseded
- Revenue notes: ~/rick-vault/revenue/YYYY-MM-DD.md
- Daily notes: ~/rick-vault/memory/YYYY-MM-DD.md
- Decisions: ~/rick-vault/decisions/YYYY-MM-DD-{slug}.md
- Weekly reviews: ~/rick-vault/weekly-reviews/YYYY-Www.md
- Executive briefings: ~/rick-vault/control/briefings/
- Scorecards: ~/rick-vault/scorecards/portfolio.json
- Memory index: ~/rick-vault/control/memory-index.json
- Memory access log: ~/rick-vault/operations/memory-access.jsonl
- Memory overview: ~/rick-vault/dashboards/memory-overview.md

## Note Types
| Type | Location | Purpose |
|------|----------|---------|
| project | projects/{name}/summary.md | Active project context |
| revenue-snapshot | revenue/YYYY-MM-DD.md | Daily revenue data |
| decision | decisions/ | Significant decisions with rationale |
| daily | memory/YYYY-MM-DD.md | Daily operational timeline |
| weekly-review | weekly-reviews/YYYY-Www.md | Sunday synthesis |
| okr | okrs/QN-YYYY.md | Quarterly objectives |
| fact | projects/{name}/items.json | Atomic facts (JSON) |
