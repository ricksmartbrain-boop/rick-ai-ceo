---
name: fleet-intelligence
description: Cross-Rick compounding via the Hive. Exports proven skill variants + dream-insight patterns (PII-scrubbed, aggregate-only), imports global-best learnings from every other Rick back into the local pool. Daily 04:00, gated by RICK_FLEET_INTEL_LIVE=1.
---

# Fleet Intelligence

Every Rick's wins become every Rick's wins. Local learning compounds across the fleet instead of staying siloed to one tenant.

## Scripts

### `export-wins.py`
Selects only well-evidenced winners:
- `skill_variants`: `status='active' AND n_runs >= 20 AND win_rate >= 0.6`
- `effective_patterns`: `pattern_kind='dream_insight' AND sum_wins >= 3`

Scrubs every exported prompt_text + evidence_json for emails, URLs, OpenAI keys (`sk-...`), license keys (`RP_...`/`RB_...`). Rows whose raw prompt_text contains an email address are skipped entirely (belt + braces). POSTs to `/api/v1/hive/learnings`. JSONL dedup ledger at `~/rick-vault/operations/hive-exports.jsonl` prevents re-posting unchanged rows within 30 days.

### `import-global-best.py`
GETs `/api/v1/hive/global-best` + `/api/v1/hive/patterns`, registers each returned variant via the idempotent `runtime.variants.register_variant()` helper with `variant_id='global_<hash>'`, and INSERTs OR IGNOREs each shared pattern with `pattern_kind='dream_insight_global'` to distinguish imports from local mining.

## Privacy

- `RICK_SECRET` only in `Authorization: Bearer` header — never logged.
- Regex scrub: `[\w.+-]+@[\w.-]+` → `[REDACTED_EMAIL]`; `https?://\S+` → `[REDACTED_URL]`; `sk-[A-Za-z0-9_-]+` → `[REDACTED_KEY]`; `R[PB]_[A-Fa-f0-9]+` → `[REDACTED_LICENSE]`.
- Length guards: prompt_text in [50, 8000] chars or skip.
- Never exports: primary keys, tenant IDs, parent_variant_id, raw counts (only aggregate win_rate + n_runs), customer content.

## Schedule + gate

`ai.rick.fleet-intelligence.plist` runs 04:00 daily. `--live` only when `RICK_FLEET_INTEL_LIVE=1`; default dry-run.

## Degradation

- POST 404/5xx/timeout → log, continue. Exit 0.
- GET 404 on global-best / patterns → "hive endpoint unavailable", exit 0 (no DB writes). When J3 endpoints deploy, sync starts automatically — no code change.
