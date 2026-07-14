# Model Orchestration

## Goal

Rick should use the strongest available model for the job without turning every task into an expensive unfocused super-prompt.

The design is:
- direct provider APIs by default
- optional OpenAI-compatible gateway via `RICK_LLM_GATEWAY_URL`
- one route per job type
- a bounded multi-provider panel for strategy work
- per-route provider fallback chains for non-strategy tasks
- durable usage logging for every generation
- configurable per-model cost estimation via `RICK_MODEL_PRICING_FILE`

## Current Route Map

As of `2026-03-06`, the recommended defaults in this workspace are:

- `strategy`
  - panel: `openai:gpt-5.4-pro`, `anthropic:claude-opus-4-6`, `google:gemini-3.1-pro-preview`
  - synthesis: `openai:gpt-5.4`
- `coding`
  - `gpt-5.4-pro`
- `writing`
  - `claude-sonnet-4-6`
- `review`
  - `claude-opus-4-6`
- `analysis`
  - `gemini-3.1-pro-preview`
- `heartbeat`
  - `gemini-3.1-flash-lite-preview`
- `research`
  - `grok-4-latest`

## Why This Shape

- `GPT-5.4 pro` is expensive and slow enough that it should be reserved for top-level decisions and the hardest engineering work.
- `GPT-5.4 pro` should not be the default for schema-critical control-plane tasks; keep it for executive memos, portfolio decisions, and high-stakes thinking.
- `Claude Opus 4.6` is excellent for critique, risk review, and deeper judgment.
- `Claude Opus 4.6` is also one of the best hard-coding and multi-file engineering specialists, so Rick should keep it as the first co-primary fallback for coding.
- `Gemini 3.1 Pro Preview` is the strongest Gemini slot for large-context synthesis and broad document analysis in the current public docs.
- `Claude Sonnet 4.6` is the writing workhorse.
- `Gemini 3.1 Flash-Lite Preview` is a better fit for recurring heartbeat parsing when you want high-frequency parsing without burning Pro capacity.
- `Grok 4` is useful for live-web/X-flavored research, not as a final truth source for money-risk actions.

## Strategy Panel

When `RICK_STRATEGY_PANEL_ENABLED=1`, route `strategy` does this:
1. send the same prompt to the panel models in parallel
2. collect only successful live responses
3. synthesize them into one recommendation
4. fall back safely if the panel is unavailable

This is intentionally bounded:
- no recursive sub-panels
- no unbounded retries
- no more than `RICK_STRATEGY_PANEL_MAX_MODELS`

## Route Fallbacks

For non-strategy routes, Rick now tries the primary model first and then walks a short fallback chain before degrading to static fallback text.

Current coding posture:
- primary hard-coding brain: `gpt-5.4-pro`
- first specialist fallback: `claude-opus-4-6`
- repo/git automation throughput lane: `gpt-5.3-codex`
- lower-cost execution fallback: `claude-sonnet-4-6`

Env overrides:
- `RICK_ROUTE_CODING_FALLBACKS`
- `RICK_ROUTE_WRITING_FALLBACKS`
- `RICK_ROUTE_REVIEW_FALLBACKS`
- `RICK_ROUTE_ANALYSIS_FALLBACKS`
- `RICK_ROUTE_HEARTBEAT_FALLBACKS`
- `RICK_ROUTE_RESEARCH_FALLBACKS`

## Required Access

Direct provider mode:
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GOOGLE_API_KEY` or `GEMINI_API_KEY`
- `XAI_API_KEY`

Optional gateway mode:
- `RICK_LLM_GATEWAY_URL`
- `RICK_LLM_GATEWAY_API_KEY`

## Token Economics

Rick now estimates USD per generation from `RICK_MODEL_PRICING_FILE`.

Default bootstrap seeds `config/model-pricing.json` from the example file. Review it before production use because provider pricing changes and your account may expose different model ids.

Use these commands to inspect routing and spend:

```bash
python3 skills/executive-orchestrator/scripts/model-router.py --list --format json
python3 skills/token-economics/scripts/token-usage.py report --write
```

## Important Note About Gemini Naming

Earlier Rick drafts used `gemini-3-pro-preview`.

Public Google docs checked on `2026-03-08` explicitly deprecate `gemini-3-pro-preview` effective `March 9, 2026` and direct developers to `gemini-3.1-pro-preview`. This workspace now uses `gemini-3.1-pro-preview` and `gemini-3.1-flash-lite-preview` by default so the defaults line up with current official naming.

If your account exposes a newer Gemini alias later, update:
- `RICK_MODEL_GOOGLE_WORKHORSE`
- `RICK_MODEL_GOOGLE_BUDGET`
- `RICK_STRATEGY_PANEL_MODELS`

## Files

- `runtime/llm.py` — live provider execution and strategy panel
- `config/rick.env.example` — env defaults and key placeholders
- `skills/executive-orchestrator/scripts/model-router.py` — route inspection
- `scripts/doctor.sh` — model/key readiness checks
