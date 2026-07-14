# Workflow Entrypoint Adoption Audit

**Generated:** 2026-05-23
**Resolver:** `scripts/workflow_identity_resolver.py`
**Registry:** `data/workflow-identity-registry.jsonl` (145 entries)

---

## Summary

| Entrypoint | File | Gated? | Notes |
|---|---|---|---|
| `queue_initiative_workflow()` | `runtime/engine.py:1943` | YES | Full gate: reuse → return, needs_review → block, create → proceed |
| `_queue_initiative()` | `skills/executive-orchestrator/scripts/initiative-scanner.py:99` | YES (patched 2026-05-23) | Previously used title-string dedup only; now calls `resolve_or_create_workflow` |
| `create_workflow(..., "info_product_launch", ...)` | `runtime/engine.py:1865` | NO | Info product launches do not use the identity gate; acceptable — title uniqueness handled by caller |
| `create_workflow(..., "fiverr_gig_launch", ...)` | `runtime/engine.py:2069` | NO | Acceptable — fiverr gigs are distinct by definition |
| `create_workflow(..., "fiverr_order", ...)` | `runtime/engine.py:2105` | NO | Acceptable — order IDs are unique |
| `create_workflow(..., "upwork_proposal", ...)` | `runtime/engine.py:2221` | NO | Acceptable — proposal IDs are unique |
| `create_workflow(...)` (generic) | `runtime/engine.py:2368` | NO | Generic passthrough; caller controls kind; only `initiative` kind causes storms |
| `create_workflow(lead, opener)` | `scripts/founder-discovery-pipeline.py:947` | NO | Different `create_workflow` (local function, not engine); no initiative kind |

---

## Gated Paths (2 of 2 initiative-creation entrypoints now covered)

### 1. `runtime/engine.py` — `queue_initiative_workflow()` (line 1943)

**Status: GATED**
Implementation:
- Calls `resolve_or_create_workflow(title, kind="initiative", rationale=...)`
- `reuse` → returns existing `db_workflow_id`, no DB write
- `needs_review` → logs warning, returns `""`, no DB write
- `create` → proceeds, back-fills `db_workflow_id` into registry

This is the primary path for all heartbeat/scheduler-driven initiative creation.

### 2. `skills/executive-orchestrator/scripts/initiative-scanner.py` — `_queue_initiative()` (line 99)

**Status: GATED (patched 2026-05-23)**
Previously: used a simple `title in existing` set check — no fuzzy dedup, no registry gate.
Now: calls `resolve_or_create_workflow` before any DB write. Blocks both exact matches (reuse) and high-similarity variants (needs_review).

---

## Ungated Paths (acceptable exceptions)

The following `create_workflow` calls are **not** routed through the identity resolver, but are **not a duplication risk** because:

- `info_product_launch`, `fiverr_gig_launch`, `fiverr_order`, `fiverr_inquiry`, `upwork_proposal`, `upwork_contract`, `upwork_message`, `upwork_post_project`, `upwork_analytics` — each of these kinds is triggered by an external event with a unique ID (order, proposal, contract). Duplicate creation would require the same external event to fire twice, which is handled by the caller's idempotency.

**Risk:** If a new `initiative`-kind path is added to engine.py or a new script without calling through `queue_initiative_workflow`, it would bypass the gate.
**Mitigation:** See enforcement assertion below.

---

## Enforcement Assertion (recommended)

Add to CI or as a pre-commit hook to prevent new bypass paths:

```bash
# Any direct create_workflow call with kind="initiative" outside queue_initiative_workflow is a sev-1
python3 -c "
import re, sys
files = [
    'runtime/engine.py',
    'skills/executive-orchestrator/scripts/initiative-scanner.py',
]
pattern = re.compile(r'create_workflow\(.*initiative.*\)')
for f in __import__('glob').glob('**/*.py', recursive=True):
    content = open(f).read()
    if 'create_workflow' in content and '\"initiative\"' in content:
        if 'resolve_or_create_workflow' not in content and 'queue_initiative_workflow' not in content:
            print(f'FAIL: {f} calls create_workflow with initiative kind without resolver gate')
            sys.exit(1)
print('OK: all initiative creation paths have resolver gate')
"
```

---

## Known Duplicate Clusters (from filesystem inventory)

| Cluster title | Kind | Dirs | Canonical action |
|---|---|---|---|
| canonical initiative registry | automation | 11 | 1 canonical; 10 aliases |
| daily ship gate | workflow | 10 | 1 canonical; 9 aliases |
| workflow family identity service | control plane | 3 | 1 canonical; 2 aliases |
| ops vs output scoreboard | dashboard | 3 | 1 canonical; 2 aliases |
| proof of work minimum | workflow | 3 | 1 canonical; 2 aliases |
| workflow completion guardrail | workflow | 3 | 1 canonical; 2 aliases |

Full detail: `reports/duplicate-clusters.md`

---

## Registry State (2026-05-23)

- **Total entries:** 145
- **Workflow dirs inventoried:** 178 (88 initiative-prefix, 90 title-prefix)
- **Exact-FP duplicate clusters:** 13
- **Fuzzy cross-title pairs (sim >= 0.70):** 4
- **Review queue entries:** see `data/workflow-identity-review-queue.jsonl`

---

## Post-Cutover Monitoring

Track for 7 days:
- Create attempts vs reuse vs needs_review counts (`python3 scripts/workflow_identity_resolver.py stats`)
- Any new workflow folder without `workflow_uid` in registry = sev-1
- Any second folder for same normalized objective = sev-1
