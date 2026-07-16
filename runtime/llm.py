#!/usr/bin/env python3
"""Route-aware text generation with direct provider APIs and strategy panels."""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.log import get_logger

_LOGGER = get_logger("rick.llm")


# ── Per-job cost tracking ────────────────────────────────────────────────────
# The 2026-04-21 audit found outcomes.cost_usd was populated 0 of 3232 times in
# the last 7 days — complete spend blindness. Root cause: log_generation writes
# cost to ~/rick-vault/operations/llm-usage.jsonl but never back to the
# outcomes row the engine writes per job. This fix threads the cost via a
# context-local accumulator so engine.py can read it at outcome INSERT time
# without every handler being refactored. Zero cost when unused (default path
# checks a null contextvar).
_CURRENT_JOB: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "rick_llm_current_job", default=None
)
_JOB_COST: dict[str, dict[str, Any]] = {}
_JOB_COST_LOCK = threading.Lock()


def begin_job_tracking(job_id: str) -> None:
    """Start accumulating LLM cost for this job_id. Called by engine.py::
    process_one_job right after mark_job('running'). All subsequent
    generate_text / strategy_panel calls within the same context record their
    cost + model + usage into this job's accumulator.
    """
    _CURRENT_JOB.set(job_id)
    with _JOB_COST_LOCK:
        _JOB_COST[job_id] = {
            "cost_usd": 0.0,
            "model": "",
            "provider": "",
            "tokens_in": 0,
            "tokens_out": 0,
            "calls": 0,
            "started_at": datetime.now().isoformat(timespec="seconds"),
        }


def get_and_clear_job_cost(job_id: str) -> dict[str, Any]:
    """Pop the accumulated cost for this job. Called by engine.py at each
    outcome INSERT site so the numbers land in the outcomes row. Returns {}
    if tracking was never started (defensive: subagent-only jobs, stub paths).
    """
    with _JOB_COST_LOCK:
        return _JOB_COST.pop(job_id, {})


def cleanup_job_tracking(job_id: str) -> None:
    """Idempotent safety-net cleanup for paths that bypass the INSERT sites
    (e.g. approval-required, dependency-blocked). Called in engine.py::
    process_one_job's finally block so a never-popped row can't leak memory
    across heartbeat cycles.
    """
    _CURRENT_JOB.set(None)
    with _JOB_COST_LOCK:
        _JOB_COST.pop(job_id, None)


# 2026-07-16 GPT-5.6 rebuild (gpt56-model-strategy-briefing-2026-07-16.md):
# judgment routes on gpt-5.6-sol, analysis on terra, heartbeat on luna.
# All gemini rungs/primaries deleted — no GOOGLE_API_KEY exists (0.45%
# lifetime success). gemini returns when GOOGLE_API_KEY is configured.
# Provider "claude-cli" is the subscription CLI rung (see call_claude_cli):
# it keeps working when ANTHROPIC_API_KEY is credit-dead.
ROUTES = {
    "strategy": {
        "env": "RICK_MODEL_OPENAI_STRATEGIC",
        "default": "gpt-5.6-sol",
        "provider": "openai",
    },
    "coding": {
        "env": "RICK_MODEL_OPENAI_CODING",
        "default": "gpt-5.6-sol",
        "provider": "openai",
    },
    "writing": {
        "env": "RICK_MODEL_ANTHROPIC_WORKHORSE",
        "default": "claude-sonnet-4-6",
        "provider": "claude-cli",
    },
    "review": {
        "env": "RICK_MODEL_OPENAI_STRATEGIC",
        "default": "gpt-5.6-sol",
        "provider": "openai",
    },
    "analysis": {
        "env": "RICK_MODEL_OPENAI_WORKHORSE",
        "default": "gpt-5.6-terra",
        "provider": "openai",
    },
    "heartbeat": {
        "env": "RICK_MODEL_OPENAI_BUDGET",
        "default": "gpt-5.6-luna",
        "provider": "openai",
    },
    "research": {
        "env": "RICK_MODEL_XAI_RESEARCH",
        "default": "grok-4-latest",
        "provider": "xai",
    },
}
ROUTE_SYSTEM_PROMPTS = {
    "strategy": (
        "You are Rick's executive strategy layer. Mission: reach $100K MRR through owned products. "
        "Prefer decisive, falsifiable plans with concrete next actions, assumptions, and risks."
    ),
    "coding": (
        "You are Rick's coding layer. Prefer precise, runnable, multi-file-safe implementation and architecture work. "
        "Do not invent repository state, command success, or test results."
    ),
    "writing": (
        "You are Rick's writing layer. Write clearly, crisply, and persuasively. Avoid hype and generic filler."
    ),
    "review": (
        "You are Rick's red-team reviewer. Find bugs, risks, missing verification, and weak assumptions first."
    ),
    "analysis": (
        "You are Rick's analysis layer. Distinguish fact from inference and compress complex context into clear decisions."
    ),
    "heartbeat": (
        "You are Rick's heartbeat parser. Return operational truth only. Never report success when dependencies are missing."
    ),
    "research": (
        "You are Rick's research layer. Prefer current, source-backed claims and clearly mark uncertainty."
    ),
}
ROUTE_MAX_OUTPUT_TOKENS: dict[str, int] = {
    "heartbeat": 2048,
    "analysis": 8192,
    "writing": 8192,  # long-form riders (seo posts 1200-1800 words, newsletter) bind on API fallback rungs; cap is runaway protection, not a spend trim
    "coding": 16384,
    "strategy": 8192,
    "review": 8192,
    "research": 8192,
}
ROUTE_REASONING_EFFORT: dict[str, str] = {
    "strategy": "high",
    "review": "high",
    "coding": "high",
    "analysis": "medium",
    "writing": "low",  # applies on OpenAI rungs only (call_openai)
    "heartbeat": "none",  # gpt-5.6 supports effort "none" — near-free ops parse
}
STRATEGY_PANEL_DEFAULTS = (
    ("openai", "RICK_MODEL_OPENAI_STRATEGIC_PRO", "gpt-5.6-sol"),
    ("anthropic", "RICK_MODEL_ANTHROPIC_STRATEGIC", "claude-opus-4-8"),  # health-gated
    ("openai", "RICK_MODEL_OPENAI_WORKHORSE", "gpt-5.6-terra"),  # gemini removed (no key)
)
# 2026-07-16 chains: rungs walk in order; the provider health gate skips
# providers with ≥3 consecutive billing/auth failures in 30 min (anthropic
# additionally seeded from billing-watchdog.jsonl). claude-cli rungs bill to
# the Claude subscription and stay live when API credits die. gemini rungs
# deleted — gemini returns when GOOGLE_API_KEY is configured.
ROUTE_FALLBACK_DEFAULTS = {
    "strategy": (
        ("anthropic", "claude-opus-4-8"),
        ("claude-cli", "claude-sonnet-4-6"),
        ("openai", "gpt-5.6-terra"),  # downgrade alert fires when a call lands here
    ),
    "coding": (
        ("claude-cli", "claude-sonnet-4-6"),
        ("openai", "gpt-5.6-terra"),
        ("openai", "gpt-5.3-codex"),
    ),
    "review": (
        ("anthropic", "claude-opus-4-8"),
        ("claude-cli", "claude-sonnet-4-6"),
        ("openai", "gpt-5.6-terra"),  # downgrade alert fires when a call lands here
    ),
    "writing": (
        ("anthropic", "claude-sonnet-4-6"),
        ("openai", "gpt-5.6-terra"),
    ),
    "analysis": (
        ("openai", "gpt-5.6-sol"),
        ("claude-cli", "claude-sonnet-4-6"),
    ),
    "heartbeat": (
        ("openai", "gpt-5.6-terra"),
    ),
    "research": (
        ("openai", "gpt-5.6-terra"),
        ("anthropic", "claude-sonnet-4-6"),
    ),
}
# Budget caps on these routes raise BudgetExceeded instead of returning canned
# fallback text (2026-07-16 fail-loud decision — canned strategy output is
# indistinguishable from real output downstream).
FAIL_LOUD_ROUTES = frozenset({"strategy", "review", "coding"})
# Rungs below the route's primary intelligence tier. Landing here is survival,
# not health — ledger note + deduped operator alert. opus-4-8 and claude-cli
# sonnet are designed same-class rungs, NOT downgrades
# (feedback_no_intelligence_downgrades).
DOWNGRADE_ALERT_RUNGS: dict[str, frozenset[tuple[str, str]]] = {
    "strategy": frozenset({("openai", "gpt-5.6-terra")}),
    "review": frozenset({("openai", "gpt-5.6-terra")}),
    "coding": frozenset({("openai", "gpt-5.6-terra"), ("openai", "gpt-5.3-codex")}),
}
KNOWN_GOOD_MODEL_IDS = frozenset({
    # Canary-proven 2026-07-16: POST /v1/responses → HTTP 200 status=completed
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
})
STRATEGY_SYNTHESIS_DEFAULT = ("openai", "RICK_STRATEGY_PANEL_SYNTHESIS_MODEL", "openai:gpt-5.6-sol")
DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
ROOT_DIR = Path(__file__).resolve().parents[1]
USAGE_FILE = Path(
    os.path.expanduser(os.getenv("RICK_LLM_USAGE_LOG_FILE", str(DATA_ROOT / "operations" / "llm-usage.jsonl")))
)
PRICING_FILE = Path(
    os.path.expanduser(os.getenv("RICK_MODEL_PRICING_FILE", str(ROOT_DIR / "config" / "model-pricing.json")))
)
REQUEST_TIMEOUT_SECONDS = int(os.getenv("RICK_LLM_REQUEST_TIMEOUT_SECONDS", "600"))
BUDGET_BUCKETS = {
    "heartbeat": "heartbeat",
    "coding": "coding",
    "research": "research",
    "strategy": "strategic",
    "review": "strategic",
    "analysis": "workhorse",
    "writing": "workhorse",
}


@dataclass
class UsageStats:
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    # 2026-07-16 cache visibility: prompt tokens read from cache
    # (usage.input_tokens_details.cached_tokens) and written to cache
    # (usage.cache_write_tokens, gpt-5.6+ bills writes at 1.25x).
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    estimated: bool = True


@dataclass
class GenerationResult:
    content: str
    route: str
    model: str
    runner: str
    mode: str
    provider: str = "unknown"
    usage: UsageStats | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class LiveCallResult:
    text: str
    runner: str
    provider: str
    usage: UsageStats | None = None


class BudgetExceeded(RuntimeError):
    """A budget cap blocked a fail-loud route (strategy/review/coding).

    2026-07-16: these routes must never silently return canned fallback text
    on a budget block — the exception propagates to the engine job runner,
    which records last_error and escalates. Other routes keep the
    canned-fallback behavior.
    """


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def resolve_model(route: str) -> str:
    route_config = ROUTES[route]
    return os.getenv(route_config["env"], route_config["default"])


def resolve_provider(route: str) -> str:
    return ROUTES[route]["provider"]


def estimate_tokens(text: str) -> int:
    return max(1, len(text.encode("utf-8")) // 4)


def parse_model_ref(value: str, fallback_provider: str | None = None) -> tuple[str, str]:
    if ":" not in value:
        provider = fallback_provider or infer_provider(value, "")
        return provider, value
    provider, model = value.split(":", 1)
    return provider.strip().lower(), model.strip()


def infer_provider(model: str, runner: str) -> str:
    lowered = model.lower()
    if "gpt" in lowered or "codex" in lowered or "o3" in lowered or "o4" in lowered:
        return "openai"
    if "claude" in lowered or runner == "anthropic":
        return "anthropic"
    if "gemini" in lowered:
        return "google"
    if "grok" in lowered:
        return "xai"
    return "unknown"


def extract_openai_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str) and payload["output_text"].strip():
        return payload["output_text"]

    parts: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            if isinstance(content.get("text"), str) and content["text"].strip():
                parts.append(content["text"])
            elif content.get("type") == "output_text" and isinstance(content.get("text"), str):
                parts.append(content["text"])

    if parts:
        return "\n".join(parts).strip()

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            fragments = [
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            ]
            return "\n".join(fragment for fragment in fragments if fragment).strip()
    return ""


def usage_from_openai(payload: dict[str, Any]) -> UsageStats | None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    details = usage.get("output_tokens_details", {})
    input_details = usage.get("input_tokens_details") or {}
    return UsageStats(
        input_tokens=int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
        output_tokens=int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0),
        reasoning_tokens=int(details.get("reasoning_tokens", 0) or 0),
        cached_tokens=int(input_details.get("cached_tokens", 0) or 0),
        cache_write_tokens=int(usage.get("cache_write_tokens", 0) or 0),
        estimated=False,
    )


def usage_from_anthropic(payload: dict[str, Any]) -> UsageStats | None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    return UsageStats(
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
        estimated=False,
    )


def usage_from_google(payload: dict[str, Any]) -> UsageStats | None:
    usage = payload.get("usageMetadata")
    if not isinstance(usage, dict):
        return None
    return UsageStats(
        input_tokens=int(usage.get("promptTokenCount", 0) or 0),
        output_tokens=int(usage.get("candidatesTokenCount", 0) or 0),
        reasoning_tokens=int(usage.get("thoughtsTokenCount", 0) or 0),
        estimated=False,
    )


def http_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int, *, _max_retries: int = 3) -> dict[str, Any]:
    import time

    request_data = json.dumps(payload).encode("utf-8")
    merged_headers = {"Content-Type": "application/json", **headers}
    for attempt in range(_max_retries):
        request = urllib.request.Request(url, data=request_data, headers=merged_headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < _max_retries - 1:
                retry_after = float(exc.headers.get("Retry-After", 0) or 0)
                backoff = max(retry_after, 2 ** (attempt + 1))
                time.sleep(backoff)
                continue
            raise
    raise RuntimeError("http_json: retries exhausted")


def system_prompt(route: str) -> str:
    return ROUTE_SYSTEM_PROMPTS.get(route, ROUTE_SYSTEM_PROMPTS["analysis"])


def gateway_url() -> str:
    base = os.getenv("RICK_LLM_GATEWAY_URL", "").strip().rstrip("/")
    if not base:
        return ""
    return base if base.endswith("/v1") else f"{base}/v1"


def call_gateway(provider: str, model: str, route: str, prompt: str) -> LiveCallResult | None:
    base = gateway_url()
    if not base:
        return None

    api_key = os.getenv("RICK_LLM_GATEWAY_API_KEY", "").strip()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt(route)},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }
    response = http_json(f"{base}/chat/completions", headers, payload, REQUEST_TIMEOUT_SECONDS)
    text = extract_openai_text(response)
    if not text:
        return None
    return LiveCallResult(text=text, runner="gateway", provider=provider, usage=usage_from_openai(response))


def call_openai(model: str, route: str, prompt: str, effort: str | None = None) -> LiveCallResult | None:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    base = os.getenv("RICK_OPENAI_API_BASE_URL", "https://api.openai.com").rstrip("/")
    payload: dict[str, Any] = {
        "model": model,
        "input": prompt,
        "instructions": system_prompt(route),
        # 2026-07-16 prompt caching: `instructions` (static per route) precedes
        # `input` in the model context, so the request is stable-prefix-first;
        # prompt_cache_key routes same-route calls to the same cache node
        # (implicit mode — explicit breakpoints need content-block input and
        # our stable prefix is far below the 1024-token cacheable minimum).
        "prompt_cache_key": f"rick:{route}:v1",
        "max_output_tokens": ROUTE_MAX_OUTPUT_TOKENS.get(route, int(os.getenv("RICK_LLM_MAX_OUTPUT_TOKENS", "4096"))),
    }
    default_effort = ROUTE_REASONING_EFFORT.get(route)
    if effort:
        # Explicit per-call escalation (e.g. xhigh on the ≥$499 deal branch)
        # wins over env + route default.
        payload["reasoning"] = {"effort": effort}
    elif default_effort or model.endswith("-pro"):
        payload["reasoning"] = {"effort": os.getenv("RICK_OPENAI_REASONING_EFFORT", default_effort or "high")}
    response = http_json(
        f"{base}/v1/responses",
        {"Authorization": f"Bearer {api_key}"},
        payload,
        int(os.getenv("RICK_OPENAI_REQUEST_TIMEOUT_SECONDS", str(REQUEST_TIMEOUT_SECONDS))),
    )
    text = extract_openai_text(response)
    if not text:
        return None
    return LiveCallResult(text=text, runner="openai-responses", provider="openai", usage=usage_from_openai(response))


def call_anthropic(model: str, route: str, prompt: str) -> LiveCallResult | None:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    base = os.getenv("RICK_ANTHROPIC_API_BASE_URL", "https://api.anthropic.com").rstrip("/")
    payload = {
        "model": model,
        "max_tokens": ROUTE_MAX_OUTPUT_TOKENS.get(route, int(os.getenv("RICK_ANTHROPIC_MAX_TOKENS", "4096"))),
        "system": [{"type": "text", "text": system_prompt(route), "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": prompt}],
    }
    response = http_json(
        f"{base}/v1/messages",
        {
            "x-api-key": api_key,
            "anthropic-version": os.getenv("RICK_ANTHROPIC_API_VERSION", "2023-06-01"),
        },
        payload,
        int(os.getenv("RICK_ANTHROPIC_REQUEST_TIMEOUT_SECONDS", str(REQUEST_TIMEOUT_SECONDS))),
    )
    content = response.get("content", [])
    text = "\n".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
    ).strip()
    if not text:
        return None
    return LiveCallResult(text=text, runner="anthropic-api", provider="anthropic", usage=usage_from_anthropic(response))


def call_google(model: str, route: str, prompt: str) -> LiveCallResult | None:
    api_key = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()
    if not api_key:
        return None
    base = os.getenv("RICK_GOOGLE_API_BASE_URL", "https://generativelanguage.googleapis.com").rstrip("/")
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt(route)}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": ROUTE_MAX_OUTPUT_TOKENS.get(route, 4096)},
    }
    response = http_json(
        f"{base}/v1beta/models/{model}:generateContent",
        {"x-goog-api-key": api_key},
        payload,
        int(os.getenv("RICK_GOOGLE_REQUEST_TIMEOUT_SECONDS", str(REQUEST_TIMEOUT_SECONDS))),
    )
    candidates = response.get("candidates", [])
    texts: list[str] = []
    if isinstance(candidates, list) and candidates:
        content = candidates[0].get("content", {})
        for part in content.get("parts", []):
            if isinstance(part, dict) and isinstance(part.get("text"), str) and part["text"].strip():
                texts.append(part["text"])
    text = "\n".join(texts).strip()
    if not text:
        return None
    return LiveCallResult(text=text, runner="google-gemini", provider="google", usage=usage_from_google(response))


def call_xai(model: str, route: str, prompt: str) -> LiveCallResult | None:
    api_key = os.getenv("XAI_API_KEY", "").strip()
    if not api_key:
        return None
    base = os.getenv("RICK_XAI_API_BASE_URL", "https://api.x.ai").rstrip("/")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt(route)},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    response = http_json(
        f"{base}/v1/chat/completions",
        {"Authorization": f"Bearer {api_key}"},
        payload,
        int(os.getenv("RICK_XAI_REQUEST_TIMEOUT_SECONDS", str(REQUEST_TIMEOUT_SECONDS))),
    )
    text = extract_openai_text(response)
    if not text:
        return None
    return LiveCallResult(text=text, runner="xai-chat", provider="xai", usage=usage_from_openai(response))


def cli_fallback(model: str, route: str, prompt: str) -> LiveCallResult | None:
    timeout = int(os.getenv("RICK_CLI_REQUEST_TIMEOUT_SECONDS", str(REQUEST_TIMEOUT_SECONDS)))

    if infer_provider(model, "") == "anthropic" and shutil.which("anthropic"):
        result = subprocess.run(
            [
                "anthropic",
                "messages",
                "create",
                "--model",
                model,
                "--max-tokens",
                str(ROUTE_MAX_OUTPUT_TOKENS.get(route, 4096)),
                "--system",
                system_prompt(route),
                "-m",
                f"user:{prompt}",
                "--no-stream",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        if result.returncode == 0 and result.stdout.strip():
            try:
                payload = json.loads(result.stdout)
                text = "\n".join(
                    block.get("text", "")
                    for block in payload.get("content", [])
                    if isinstance(block, dict) and isinstance(block.get("text"), str)
                ).strip()
            except (json.JSONDecodeError, TypeError):
                text = ""
            if text:
                return LiveCallResult(
                    text=text,
                    runner="anthropic-cli",
                    provider="anthropic",
                    usage=usage_from_anthropic(payload),
                )

    if infer_provider(model, "") == "anthropic":
        return call_claude_cli(model, route, prompt)
    return None


def call_claude_cli(model: str, route: str, prompt: str) -> LiveCallResult | None:
    """Subscription-billed `claude --print` rung — first-class chain member.

    This is the existing path that produced every runner=claude-cli ledger row
    (267 lifetime, latest success 2026-07-16 04:49). It bills the Claude
    subscription, not ANTHROPIC_API_KEY, so it stays live when API credits are
    dead. --model pins the rung's model id so the ledger row matches what ran.
    """
    if not shutil.which("claude"):
        return None
    timeout = int(os.getenv("RICK_CLI_REQUEST_TIMEOUT_SECONDS", str(REQUEST_TIMEOUT_SECONDS)))
    # The CLI prefers an exported ANTHROPIC_API_KEY over its subscription
    # login; with rick.env sourced that key is credit-dead and the rung fails
    # ("Credit balance is too low"). Strip it so the subscription always bills.
    cli_env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    result = subprocess.run(
        ["claude", "--print", "--model", model, prompt],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        env=cli_env,
    )
    if result.returncode == 0 and result.stdout.strip():
        return LiveCallResult(text=result.stdout.strip(), runner="claude-cli", provider="anthropic")
    return None


def run_live_generation(provider: str, model: str, route: str, prompt: str, effort: str | None = None) -> LiveCallResult | None:
    if _circuit_is_open(provider):
        _LOGGER.warning("Circuit breaker open for %s — skipping", provider)
        if provider == "claude-cli":
            return None
        return cli_fallback(model, route, prompt)
    try:
        if provider == "claude-cli":
            # Subscription rung — never routed through the gateway; it must
            # exercise the real CLI so it stays live when API credits die.
            result = call_claude_cli(model, route, prompt)
        else:
            gateway_result = call_gateway(provider, model, route, prompt)
            if gateway_result:
                _circuit_record_success(provider)
                _provider_health_record_success(provider)
                return gateway_result

            result = None
            if provider == "openai":
                result = call_openai(model, route, prompt, effort=effort)
            elif provider == "anthropic":
                result = call_anthropic(model, route, prompt) or cli_fallback(model, route, prompt)
            elif provider == "google":
                result = call_google(model, route, prompt)
            elif provider == "xai":
                result = call_xai(model, route, prompt)

        if result:
            _circuit_record_success(provider)
            _provider_health_record_success(provider)
        else:
            _circuit_record_failure(provider)
        return result
    except (
        TimeoutError,
        subprocess.TimeoutExpired,
        urllib.error.HTTPError,
        urllib.error.URLError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
    ) as exc:
        _circuit_record_failure(provider)
        if _is_billing_auth_error(exc):
            _record_provider_billing_failure(provider)
        if provider == "claude-cli":
            return None
        return cli_fallback(model, route, prompt)


def log_generation(
    route: str,
    model: str,
    runner: str,
    mode: str,
    prompt: str,
    content: str,
    provider: str,
    usage: UsageStats | None = None,
    notes: list[str] | None = None,
) -> None:
    usage_stats = usage or UsageStats(
        input_tokens=estimate_tokens(prompt),
        output_tokens=estimate_tokens(content),
        estimated=True,
    )
    usd_cost = estimate_generation_cost(model, provider, usage_stats)
    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "bucket": BUDGET_BUCKETS.get(route, "workhorse"),
        "provider": provider,
        "model": model,
        "usd": round(usd_cost, 6),
        "input_tokens": usage_stats.input_tokens or estimate_tokens(prompt),
        "output_tokens": usage_stats.output_tokens or estimate_tokens(content),
        "reasoning_tokens": usage_stats.reasoning_tokens,
        "cached_tokens": usage_stats.cached_tokens,
        "cache_write_tokens": usage_stats.cache_write_tokens,
        "task": route,
        "project": "",
        "status": "done" if mode != "error" else "failed",
        "runner": runner,
        "mode": mode,
        "estimated": usage_stats.estimated,
        "notes": notes or [],
    }
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with USAGE_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")

    # Keep in-memory spend cache in sync (skip failed requests)
    if mode != "error":
        with _daily_spend_lock:
            today = datetime.now().strftime("%Y-%m-%d")
            if _daily_spend_cache["date"] == today:
                _daily_spend_cache["total"] += usd_cost

    # Also accumulate into the current job's cost tracker if engine started one.
    # Enables outcomes.cost_usd / model_used / duration_seconds to be populated.
    if mode != "error":
        active_job = _CURRENT_JOB.get()
        if active_job:
            with _JOB_COST_LOCK:
                acc = _JOB_COST.get(active_job)
                if acc is not None:
                    acc["cost_usd"] += usd_cost
                    acc["model"] = model
                    acc["provider"] = provider
                    acc["tokens_in"] += int(usage_stats.input_tokens or 0)
                    acc["tokens_out"] += int(usage_stats.output_tokens or 0)
                    acc["calls"] += 1


def finalize(
    route: str,
    model: str,
    runner: str,
    mode: str,
    prompt: str,
    content: str,
    provider: str,
    usage: UsageStats | None = None,
    notes: list[str] | None = None,
) -> GenerationResult:
    normalized = content.rstrip()
    if normalized:
        normalized += "\n"
    log_generation(route, model, runner, mode, prompt, normalized, provider, usage=usage, notes=notes)
    return GenerationResult(
        content=normalized,
        route=route,
        model=model,
        runner=runner,
        mode=mode,
        provider=provider,
        usage=usage,
        notes=notes or [],
    )


_pricing_config_cache: tuple[float, dict[str, Any]] | None = None


def load_pricing_config() -> dict[str, Any]:
    global _pricing_config_cache  # noqa: PLW0603
    if not PRICING_FILE.exists():
        return {}

    mtime = PRICING_FILE.stat().st_mtime
    if _pricing_config_cache is not None and _pricing_config_cache[0] == mtime:
        return _pricing_config_cache[1]

    try:
        payload = json.loads(PRICING_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    result = payload if isinstance(payload, dict) else {}
    _pricing_config_cache = (mtime, result)
    return result


def parse_price(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def pricing_for_model(model: str, provider: str) -> dict[str, float]:
    payload = load_pricing_config()
    prices: dict[str, float] = {}

    model_overrides = payload.get("models", {})
    if isinstance(model_overrides, dict):
        exact = model_overrides.get(model)
        if isinstance(exact, dict):
            for key in ("input", "output", "reasoning"):
                value = parse_price(exact.get(key))
                if value is not None:
                    prices[key] = value

        if not prices:
            for prefix, candidate in model_overrides.items():
                if not isinstance(prefix, str) or not isinstance(candidate, dict):
                    continue
                if prefix.endswith("*") and model.startswith(prefix[:-1]):
                    for key in ("input", "output", "reasoning"):
                        value = parse_price(candidate.get(key))
                        if value is not None:
                            prices[key] = value
                    break

    provider_defaults = payload.get("providers", {})
    if isinstance(provider_defaults, dict):
        provider_prices = provider_defaults.get(provider)
        if isinstance(provider_prices, dict):
            for key in ("input", "output", "reasoning"):
                if key in prices:
                    continue
                value = parse_price(provider_prices.get(key))
                if value is not None:
                    prices[key] = value

    if "reasoning" not in prices and "output" in prices:
        prices["reasoning"] = prices["output"]

    return prices


def estimate_generation_cost(model: str, provider: str, usage: UsageStats) -> float:
    prices = pricing_for_model(model, provider)
    if not prices:
        # 2026-07-16: unknown models used to price at $0.00 — silent spend
        # blindness. Price at Sol rates (worst realistic case) and warn.
        _LOGGER.warning(
            "estimate_generation_cost: unknown model %s (provider=%s) — pricing at Sol rates ($5/$30 per 1M)",
            model,
            provider,
        )
        prices = {"input": 5.0, "output": 30.0, "reasoning": 30.0}

    input_rate = prices.get("input", 0.0)
    output_rate = prices.get("output", 0.0)
    reasoning_rate = prices.get("reasoning", output_rate)

    return (
        (usage.input_tokens / 1_000_000) * input_rate
        + (usage.output_tokens / 1_000_000) * output_rate
        + (usage.reasoning_tokens / 1_000_000) * reasoning_rate
    )


def generate_candidate(
    route: str,
    prompt: str,
    provider: str,
    model: str,
    fallback_text: str,
    allow_fallback: bool,
    effort: str | None = None,
) -> GenerationResult:
    if os.getenv("RICK_LLM_FALLBACK_ONLY", "").strip() == "1":
        return finalize(route, model, "fallback", "fallback", prompt, fallback_text, provider)

    if effort is None:
        live = run_live_generation(provider, model, route, prompt)
    else:
        live = run_live_generation(provider, model, route, prompt, effort=effort)
    if live and live.text.strip():
        return finalize(route, model, live.runner, "live", prompt, live.text, live.provider, usage=live.usage)

    if allow_fallback:
        return finalize(route, model, "fallback", "fallback", prompt, fallback_text, provider)
    return finalize(route, model, "error", "error", prompt, "", provider, notes=["live_generation_failed"])


def strategy_panel_enabled() -> bool:
    return parse_bool(os.getenv("RICK_STRATEGY_PANEL_ENABLED"), default=True)


def strategy_panel_refs() -> list[tuple[str, str]]:
    raw = os.getenv("RICK_STRATEGY_PANEL_MODELS", "").strip()
    if raw:
        refs = [parse_model_ref(item.strip(), fallback_provider="openai") for item in raw.split(",") if item.strip()]
    else:
        refs = [
            (provider, os.getenv(env_name, default_model))
            for provider, env_name, default_model in STRATEGY_PANEL_DEFAULTS
        ]
    max_models = int(os.getenv("RICK_STRATEGY_PANEL_MAX_MODELS", "3"))
    return refs[:max(1, max_models)]


def strategy_synthesis_ref() -> tuple[str, str]:
    provider, env_name, default_value = STRATEGY_SYNTHESIS_DEFAULT
    return parse_model_ref(os.getenv(env_name, default_value), fallback_provider=provider)


def route_fallback_refs(route: str, primary_provider: str, primary_model: str) -> list[tuple[str, str]]:
    env_name = f"RICK_ROUTE_{route.upper()}_FALLBACKS"
    raw = os.getenv(env_name, "").strip()
    if raw:
        refs = [parse_model_ref(item.strip(), fallback_provider=primary_provider) for item in raw.split(",") if item.strip()]
    else:
        refs = list(ROUTE_FALLBACK_DEFAULTS.get(route, ()))

    unique_refs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = {(primary_provider, primary_model)}
    for provider, model in refs:
        ref = (provider, model)
        if ref in seen:
            continue
        seen.add(ref)
        unique_refs.append(ref)
    return unique_refs


def _emit_silent_failure_event(route: str, refs_failed: list[tuple[str, str]], primary: tuple[str, str]) -> None:
    """Surface silent primary-model failures so they stop being silent.

    Per the 2026-04-22 audit (89% of LLM calls were running in silent fallback —
    primary model failed but heartbeat still returned HEARTBEAT_OK because the
    fallback delivered something). Now we log each failure to a dedicated JSONL
    that's easy to grep + queryable for daily summary."""
    try:
        from datetime import datetime
        log_path = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault"))) / "operations" / "llm-fallback-events.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "route": route,
                "primary": f"{primary[0]}:{primary[1]}",
                "failed": [f"{p}:{m}" for p, m in refs_failed],
                "n_failed": len(refs_failed),
            }) + "\n")
    except Exception:
        pass


def _fire_llm_alert(kind: str, text: str, dedup_window_hours: int = 24) -> None:
    """Send an operator alert through Rick's existing deduped Telegram path.

    Reuses engine.notify_operator_deduped (notification_dedupe table) — same
    lazy-import pattern as fenix_gate/proactive. Best-effort: alert delivery
    failure must never break generation.
    """
    try:
        from runtime.db import connect
        from runtime.engine import notify_operator_deduped

        connection = connect()
        try:
            notify_operator_deduped(
                connection,
                text,
                kind=kind,
                dedup_window_hours=dedup_window_hours,
                purpose="ops",
            )
        finally:
            connection.close()
    except Exception as exc:  # noqa: BLE001 — alerting is best-effort
        _LOGGER.warning("llm alert delivery failed (kind=%s): %s", kind, exc)


# ── Startup model-id sanity ──────────────────────────────────────────────────
# gpt-5.6-sol sat as the strategy/coding "default" for 2 days with 0 ledger
# calls before anyone noticed (2026-07-16 audit). Validate every configured
# model id against the pricing file / known-good set once per process, at
# first use. Local-only — NO network calls (daemon cycles every 120s).
_model_ids_validated = False
_model_ids_lock = threading.Lock()


def validate_configured_model_ids() -> list[str]:
    """Return configured model ids missing from pricing + known-good set.

    Every unknown id gets a CRITICAL log; one deduped operator alert covers
    the whole batch.
    """
    payload = load_pricing_config()
    models = payload.get("models", {})
    priced = set(models.keys()) if isinstance(models, dict) else set()

    def known(model: str) -> bool:
        if model in KNOWN_GOOD_MODEL_IDS or model in priced:
            return True
        return any(prefix.endswith("*") and model.startswith(prefix[:-1]) for prefix in priced)

    configured: set[str] = set()
    for route in ROUTES:
        provider = resolve_provider(route)
        model = resolve_model(route)
        configured.add(model)
        configured.update(m for _, m in route_fallback_refs(route, provider, model))
    configured.update(m for _, m in strategy_panel_refs())
    configured.add(strategy_synthesis_ref()[1])

    unknown = sorted(m for m in configured if not known(m))
    for model in unknown:
        _LOGGER.critical(
            "unknown model id configured: %s — not in %s or KNOWN_GOOD_MODEL_IDS",
            model,
            PRICING_FILE,
        )
    if unknown:
        _fire_llm_alert(
            "llm_unknown_model_id",
            "LLM config sanity: unknown model id(s) configured: "
            + ", ".join(unknown)
            + " — not in model-pricing.json or the known-good set. Fix the id or add pricing.",
        )
    return unknown


def _ensure_model_ids_validated() -> None:
    global _model_ids_validated  # noqa: PLW0603
    if _model_ids_validated:
        return
    with _model_ids_lock:
        if _model_ids_validated:
            return
        _model_ids_validated = True
        try:
            validate_configured_model_ids()
        except Exception as exc:  # noqa: BLE001 — sanity check must never block generation
            _LOGGER.warning("model-id validation errored: %s", exc)


def _run_chain_once(
    route: str,
    prompt: str,
    refs: list[tuple[str, str]],
    primary_provider: str,
    primary_model: str,
    notes: list[str],
    effort: str | None = None,
) -> GenerationResult | None:
    """Attempt every ref in order; return result on first success, None on full exhaustion.

    Appends live_failed / route_fallback_used / health_gate_skipped entries to
    *notes* in-place so the caller accumulates failure history across retry
    attempts. Rungs whose provider is cooling (≥3 consecutive billing/auth
    failures in 30 min) are skipped without burning a call.
    """
    failed: list[tuple[str, str]] = []
    for index, (provider, model) in enumerate(refs):
        if _provider_cooling(provider):
            notes.append(f"health_gate_skipped={provider}:{model}")
            continue
        # effort forwarded only when set so patched test doubles with the
        # original 4-arg run_live_generation signature keep working.
        if effort is None:
            live = run_live_generation(provider, model, route, prompt)
        else:
            live = run_live_generation(provider, model, route, prompt, effort=effort)
        if live and live.text.strip():
            if index > 0:
                notes.append(f"route_fallback_used={provider}:{model}")
                _emit_silent_failure_event(route, failed, (primary_provider, primary_model))
                if (provider, model) in DOWNGRADE_ALERT_RUNGS.get(route, ()):
                    # Landed below the primary intelligence tier — ledger note
                    # + deduped operator ping (fail loud, never silently).
                    notes.append(f"downgrade_below_primary={provider}:{model}")
                    _fire_llm_alert(
                        f"llm_downgrade_{route}",
                        f"LLM downgrade: {route} call landed on {provider}:{model} "
                        f"(below primary {primary_provider}:{primary_model}). "
                        "Check provider credits/health.",
                    )
            return finalize(
                route, model, live.runner, "live", prompt, live.text, live.provider,
                usage=live.usage, notes=notes,
            )
        notes.append(f"live_failed={provider}:{model}")
        failed.append((provider, model))
    # Full exhaustion — emit event and signal caller.
    _emit_silent_failure_event(route, failed, (primary_provider, primary_model))
    return None


def generate_route_with_fallbacks(
    route: str,
    prompt: str,
    fallback: str,
    primary_provider: str,
    primary_model: str,
    effort: str | None = None,
) -> GenerationResult:
    if os.getenv("RICK_LLM_FALLBACK_ONLY", "").strip() == "1":
        return finalize(route, primary_model, "fallback", "fallback", prompt, fallback, primary_provider)

    notes: list[str] = []
    refs = [(primary_provider, primary_model), *route_fallback_refs(route, primary_provider, primary_model)]

    # First attempt — existing behaviour.
    result = _run_chain_once(route, prompt, refs, primary_provider, primary_model, notes, effort=effort)
    if result is not None:
        return result

    # All refs failed.  Record in the sliding window, then check whether enough
    # recent failures have accumulated to justify sleeping before a retry.
    # This prevents burning sleep budget on a one-off transient miss.
    _record_chain_failure()

    retried = 0
    for retry_num in range(1, MAX_RETRIES_PER_CALL + 1):
        if not _should_sleep_and_retry():
            break  # window below threshold — don't burn sleep budget
        retried += 1
        _log_retry_event(route, retry_num, len(refs), RETRY_SLEEP_SECS, outcome="retrying")
        time.sleep(RETRY_SLEEP_SECS)
        # Smart-models invariant: retry always walks the FULL chain, not just
        # the cheapest model.  Notes accumulate across attempts for auditability.
        result = _run_chain_once(route, prompt, refs, primary_provider, primary_model, notes, effort=effort)
        if result is not None:
            _log_retry_event(route, retry_num, len(refs), 0, outcome="recovered")
            return result
        _record_chain_failure()

    if retried > 0:
        _log_retry_event(route, retried, len(refs), 0, outcome="exhausted")

    # Hard fallback — all attempts including retries exhausted.
    return finalize(route, primary_model, "fallback", "fallback", prompt, fallback, primary_provider, notes=notes)


def build_strategy_synthesis_prompt(original_prompt: str, panel_results: list[GenerationResult]) -> str:
    sections = []
    for index, result in enumerate(panel_results, start=1):
        sections.append(
            "\n".join(
                [
                    f"## Panel Opinion {index}",
                    f"Provider: {result.provider}",
                    f"Model: {result.model}",
                    result.content.strip(),
                ]
            )
        )
    opinions = "\n\n".join(section for section in sections if section.strip())
    return "\n".join(
        [
            "You are Rick's executive synthesis layer.",
            "Mission: reach $100K MRR through owned products.",
            "Combine the panel opinions into one decisive recommendation.",
            "Requirements:",
            "- choose the highest-expected-value plan",
            "- explain the decisive reason if the panel disagrees",
            "- call out assumptions and unknowns",
            "- give a short list of immediate actions",
            "",
            "Original prompt:",
            original_prompt,
            "",
            "Panel opinions:",
            opinions,
            "",
            "Return markdown with these sections:",
            "# Strategic Recommendation",
            "## Why This Wins",
            "## Immediate Actions",
            "## Risks / Unknowns",
        ]
    )


def generate_strategy_panel(route: str, prompt: str, fallback: str, effort: str | None = None) -> GenerationResult:
    notes: list[str] = []
    refs: list[tuple[str, str]] = []
    for provider, model in strategy_panel_refs():
        # Health gate: don't burn a panel seat on a provider that is cooling
        # (e.g. credit-dead anthropic → opus member skipped).
        if _provider_cooling(provider):
            notes.append(f"health_gate_skipped={provider}:{model}")
        else:
            refs.append((provider, model))
    ordered_results: list[GenerationResult | None] = [None] * len(refs)
    # effort forwarded only when set so patched test doubles with the
    # original 6-arg generate_candidate signature keep working.
    _effort_kwargs: dict[str, str] = {} if effort is None else {"effort": effort}

    with ThreadPoolExecutor(max_workers=max(1, len(refs))) as executor:
        future_map = {
            executor.submit(generate_candidate, route, prompt, provider, model, "", False, **_effort_kwargs): (index, provider, model)
            for index, (provider, model) in enumerate(refs)
        }
        for future in as_completed(future_map):
            index, provider, model = future_map[future]
            try:
                result = future.result()
            except Exception as exc:
                notes.append(f"{provider}:{model}=exception:{type(exc).__name__}")
                continue
            ordered_results[index] = result
            if result.mode != "live":
                notes.append(f"{provider}:{model}=failed")

    panel_results = [result for result in ordered_results if result and result.mode == "live" and result.content.strip()]
    if not panel_results:
        synthesis_provider, synthesis_model = strategy_synthesis_ref()
        return finalize(
            route,
            synthesis_model,
            "fallback",
            "fallback",
            prompt,
            fallback,
            synthesis_provider,
            notes=notes or ["strategy_panel_empty"],
        )

    synthesis_provider, synthesis_model = strategy_synthesis_ref()
    synthesis_prompt = build_strategy_synthesis_prompt(prompt, panel_results)
    synthesis_result = generate_candidate(route, synthesis_prompt, synthesis_provider, synthesis_model, "", False, **_effort_kwargs)
    if synthesis_result.mode == "live" and synthesis_result.content.strip():
        return GenerationResult(
            content=synthesis_result.content,
            route=route,
            model=synthesis_result.model,
            runner="strategy-panel",
            mode=synthesis_result.mode,
            provider=synthesis_result.provider,
            usage=synthesis_result.usage,
            notes=notes + [f"panel_models={','.join(result.model for result in panel_results)}"],
        )

    merged = "\n\n".join(
        [
            "# Strategy Panel",
            *[
                "\n".join(
                    [
                        f"## {result.provider}:{result.model}",
                        result.content.strip(),
                    ]
                )
                for result in panel_results
            ],
        ]
    ).strip()
    return finalize(
        route,
        ",".join(result.model for result in panel_results),
        "strategy-panel",
        "panel-merge",
        prompt,
        merged,
        "multi",
        notes=notes or ["strategy_panel_merged_without_synthesis"],
    )


_daily_spend_cache: dict[str, float] = {"date": "", "total": 0.0}
_daily_spend_lock = threading.Lock()

# Circuit breaker: track consecutive failures per provider
_circuit_breaker: dict[str, dict] = {}  # provider -> {"failures": int, "opened_at": float}
_circuit_breaker_lock = threading.Lock()
_CIRCUIT_BREAKER_THRESHOLD = 3
_CIRCUIT_BREAKER_COOLDOWN = 60.0  # 1 minute


def _circuit_is_open(provider: str) -> bool:
    """Check if a provider's circuit breaker is open (should be skipped)."""
    import time
    with _circuit_breaker_lock:
        state = _circuit_breaker.get(provider)
        if not state or state["failures"] < _CIRCUIT_BREAKER_THRESHOLD:
            return False
        elapsed = time.monotonic() - state["opened_at"]
        if elapsed >= _CIRCUIT_BREAKER_COOLDOWN:
            # Cooldown expired — reset and allow
            _circuit_breaker.pop(provider, None)
            return False
        return True


def _circuit_record_failure(provider: str) -> None:
    """Record a failure for a provider."""
    import time
    with _circuit_breaker_lock:
        state = _circuit_breaker.get(provider)
        if state is None:
            _circuit_breaker[provider] = {"failures": 1, "opened_at": time.monotonic()}
        else:
            state["failures"] += 1
            if state["failures"] >= _CIRCUIT_BREAKER_THRESHOLD:
                state["opened_at"] = time.monotonic()


def _circuit_record_success(provider: str) -> None:
    """Reset a provider's circuit breaker on success."""
    with _circuit_breaker_lock:
        _circuit_breaker.pop(provider, None)


# ── Provider health gate (billing/auth) ──────────────────────────────────────
# 2026-07-16 briefing: the fallback walker skips a provider's rungs after ≥3
# consecutive billing/auth failures within 30 min (credit-dead Anthropic burned
# 2,490 guaranteed failures Jul 5→16). Unlike the in-memory circuit breaker
# above, counters persist in DATA_ROOT/operations/provider-health.json so
# daemon restarts don't forget. Anthropic is additionally seeded from the
# billing watchdog: a fresh credits_low event in billing-watchdog.jsonl means
# the API is cooling before we burn even one call. One operator alert per
# cooldown entry; a successful call on the provider clears its entry
# (auto-revive after top-up).
PROVIDER_HEALTH_FILE = DATA_ROOT / "operations" / "provider-health.json"
BILLING_WATCHDOG_FILE = DATA_ROOT / "operations" / "billing-watchdog.jsonl"
PROVIDER_HEALTH_FAILURE_THRESHOLD = 3
PROVIDER_HEALTH_WINDOW_SECS = 30 * 60
_provider_health_lock = threading.Lock()


def _load_provider_health() -> dict[str, Any]:
    try:
        payload = json.loads(PROVIDER_HEALTH_FILE.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_provider_health(state: dict[str, Any]) -> None:
    try:
        PROVIDER_HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        # tmp + rename so parallel daemon workers never read a torn file
        # (same pattern as _dedup_store).
        tmp = PROVIDER_HEALTH_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(PROVIDER_HEALTH_FILE)
    except OSError as exc:
        _LOGGER.warning("provider-health.json write failed: %s", exc)


def _seconds_since(iso_ts: str) -> float | None:
    try:
        parsed = datetime.fromisoformat(iso_ts)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return (datetime.now() - parsed).total_seconds()
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def _is_billing_auth_error(exc: Exception) -> bool:
    """True for HTTP failures that mean money/credentials, not transport."""
    if not isinstance(exc, urllib.error.HTTPError):
        return False
    if exc.code in (401, 402, 403):
        return True
    if exc.code == 400:
        # Anthropic reports credit exhaustion as 400 (see billing-watchdog).
        try:
            body = exc.read().decode("utf-8", "replace").lower()
        except (OSError, ValueError):
            return False
        return "credit" in body or "billing" in body
    return False


def _record_provider_billing_failure(provider: str) -> None:
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    crossed = False
    with _provider_health_lock:
        state = _load_provider_health()
        entry = state.get(provider)
        if not isinstance(entry, dict):
            entry = {}
        age = _seconds_since(str(entry.get("last_failure_at", "")))
        if age is None or age > PROVIDER_HEALTH_WINDOW_SECS:
            entry = {"failures": 0, "first_failure_at": now_iso}
        # A real API failure now drives this entry — drop the watchdog-seed
        # marker so the counter path (not the watchdog) governs the gate.
        entry.pop("source", None)
        entry["failures"] = int(entry.get("failures", 0)) + 1
        entry["last_failure_at"] = now_iso
        if entry["failures"] >= PROVIDER_HEALTH_FAILURE_THRESHOLD and not entry.get("alerted"):
            entry["cooling_since"] = now_iso
            entry["alerted"] = True
            crossed = True
        state[provider] = entry
        _save_provider_health(state)
    if crossed:
        _fire_llm_alert(
            f"llm_provider_cooldown_{provider}",
            f"LLM provider cooldown: {provider} hit {PROVIDER_HEALTH_FAILURE_THRESHOLD} "
            "consecutive billing/auth failures within 30 min — skipping its rungs "
            "until a call succeeds or the failures age out.",
        )


def _provider_health_record_success(provider: str) -> None:
    with _provider_health_lock:
        state = _load_provider_health()
        if provider in state:
            state.pop(provider, None)
            _save_provider_health(state)


def _watchdog_says_anthropic_cooling() -> tuple[bool, str]:
    """Read the billing watchdog tail: fresh credits_low → (True, ts).

    The most recent decisive event wins — probe_ok / disabled_cleared mean
    healthy; credits_low within the health window means cooling.
    """
    try:
        with BILLING_WATCHDOG_FILE.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 8192))
            tail = handle.read().decode("utf-8", "replace")
    except OSError:
        return False, ""
    for line in reversed(tail.strip().splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("event")
        if kind in ("probe_ok", "disabled_cleared"):
            return False, ""
        if kind == "credits_low":
            ts = str(event.get("ts", ""))
            age = _seconds_since(ts)
            if age is not None and age <= PROVIDER_HEALTH_WINDOW_SECS:
                return True, ts
            return False, ""
    return False, ""


def _seed_anthropic_cooldown(watchdog_ts: str) -> None:
    """Persist a watchdog-seeded anthropic cooldown; alert once per entry."""
    fire = False
    with _provider_health_lock:
        state = _load_provider_health()
        entry = state.get("anthropic")
        if not (isinstance(entry, dict) and entry.get("alerted")):
            state["anthropic"] = {
                "failures": PROVIDER_HEALTH_FAILURE_THRESHOLD,
                "first_failure_at": watchdog_ts,
                "last_failure_at": watchdog_ts,
                "cooling_since": watchdog_ts,
                "alerted": True,
                "source": "billing-watchdog",
            }
            _save_provider_health(state)
            fire = True
    if fire:
        _fire_llm_alert(
            "llm_provider_cooldown_anthropic",
            "LLM provider cooldown: anthropic API is credit-dead per billing-watchdog "
            "(fresh credits_low) — skipping anthropic API rungs. "
            "claude-cli subscription rungs stay live.",
        )


def _provider_cooling(provider: str) -> bool:
    """True when the fallback walker should skip this provider's rungs."""
    with _provider_health_lock:
        entry = _load_provider_health().get(provider)
    # Watchdog-seeded entries defer to the watchdog below — a probe_ok must
    # reopen the gate immediately (auto-revive after top-up), so only
    # counter-driven entries close the gate here.
    entry_is_seeded = isinstance(entry, dict) and entry.get("source") == "billing-watchdog"
    if (
        not entry_is_seeded
        and isinstance(entry, dict)
        and int(entry.get("failures", 0)) >= PROVIDER_HEALTH_FAILURE_THRESHOLD
    ):
        age = _seconds_since(str(entry.get("last_failure_at", "")))
        if age is not None and age <= PROVIDER_HEALTH_WINDOW_SECS:
            return True
    if provider == "anthropic":
        cooling, watchdog_ts = _watchdog_says_anthropic_cooling()
        if cooling:
            _seed_anthropic_cooldown(watchdog_ts)
            return True
        # Watchdog reports healthy — clear any watchdog-seeded entry so the
        # next real cooldown alerts again.
        if entry_is_seeded:
            _provider_health_record_success("anthropic")
    return False


# ── Chain-failure retry layer ─────────────────────────────────────────────────
# During transient full-provider outages (e.g. Anthropic billing-skip + Google
# + OpenAI all failing simultaneously — seen 2026-04-30 02:59, 80 consecutive
# review-route exhaustions), the full chain can exhaust without any result.
# Most billing-skips clear within 30–90 s, so a single 60 s sleep + retry
# catches the majority of incidents.
#
# Smart-models invariant: each retry always attempts the FULL chain in order;
# never retries only the cheapest model. Cap: MAX_RETRIES_PER_CALL per call to
# prevent cost storms during extended outages.
#
# Retry log: ~/rick-vault/operations/llm-retry-events.jsonl

_chain_fail_window: deque = deque()  # monotonic timestamps of full-chain exhaustions
_chain_fail_lock = threading.Lock()
_CHAIN_FAIL_WINDOW_SECS = 300  # 5-minute sliding window
_CHAIN_FAIL_THRESHOLD = 3      # ≥N exhaustions in window → sleep+retry eligible

RETRY_LOG_FILE = Path(
    os.path.expanduser(
        os.getenv(
            "RICK_LLM_RETRY_LOG_FILE",
            str(DATA_ROOT / "operations" / "llm-retry-events.jsonl"),
        )
    )
)
MAX_RETRIES_PER_CALL = 3
RETRY_SLEEP_SECS = int(os.getenv("RICK_LLM_RETRY_SLEEP_SECS", "60"))


def _record_chain_failure() -> None:
    """Record a full-chain exhaustion in the sliding window."""
    with _chain_fail_lock:
        now = time.monotonic()
        _chain_fail_window.append(now)
        # Prune entries older than the window.
        while _chain_fail_window and now - _chain_fail_window[0] > _CHAIN_FAIL_WINDOW_SECS:
            _chain_fail_window.popleft()


def _should_sleep_and_retry() -> bool:
    """Return True if ≥3 full-chain exhaustions occurred in the last 5 min."""
    with _chain_fail_lock:
        now = time.monotonic()
        while _chain_fail_window and now - _chain_fail_window[0] > _CHAIN_FAIL_WINDOW_SECS:
            _chain_fail_window.popleft()
        return len(_chain_fail_window) >= _CHAIN_FAIL_THRESHOLD


def _log_retry_event(
    route: str,
    retry_num: int,
    n_refs: int,
    slept_secs: int,
    outcome: str = "retrying",
) -> None:
    """Append one retry event to llm-retry-events.jsonl.

    outcome: "retrying" | "recovered" | "exhausted"
    """
    try:
        RETRY_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with RETRY_LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "event": "chain_retry",
                        "route": route,
                        "retry_num": retry_num,
                        "n_refs_in_chain": n_refs,
                        "slept_secs": slept_secs,
                        "outcome": outcome,
                    }
                )
                + "\n"
            )
    except Exception:  # noqa: BLE001
        pass


def daily_spend_usd() -> float:
    """Sum today's LLM spend from the usage log. Uses an in-memory cache
    that is reset on date change and updated by log_generation()."""
    with _daily_spend_lock:
        today = datetime.now().strftime("%Y-%m-%d")
        if _daily_spend_cache["date"] == today:
            return _daily_spend_cache["total"]

        # Date changed or first call — full rescan
        total = 0.0
        if USAGE_FILE.exists():
            try:
                with USAGE_FILE.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        ts = entry.get("timestamp", "")
                        if isinstance(ts, str) and ts.startswith(today):
                            try:
                                total += float(entry.get("usd", 0))
                            except (TypeError, ValueError):
                                continue
            except OSError:
                pass

        _daily_spend_cache["date"] = today
        _daily_spend_cache["total"] = total
        return total


def _get_daily_cap() -> float:
    """Parse daily LLM spend cap from env. Returns 0 to disable.

    Fail-safe default matches the configured rick.env value ($15): a consumer
    launched without sourcing rick.env used to silently inherit a $500 cap.
    """
    try:
        return float(os.getenv("RICK_LLM_DAILY_CAP_USD", "15"))
    except (TypeError, ValueError):
        return 15.0


def check_daily_budget(route: str) -> tuple[bool, float]:
    """Return (allowed, current_spend). Heartbeat is always allowed.

    Cap is controlled by RICK_LLM_DAILY_CAP_USD (default: 15, 0 = unlimited).
    """
    spent = daily_spend_usd()
    if route == "heartbeat":
        return True, spent
    cap = _get_daily_cap()
    if cap <= 0:
        return True, spent
    return spent < cap, spent


_route_budget_cache: tuple[float, dict] | None = None


def load_route_budgets() -> dict:
    """Load per-route token budgets from config/token-budgets.json."""
    global _route_budget_cache  # noqa: PLW0603
    budget_file = ROOT_DIR / "config" / "token-budgets.json"
    if not budget_file.exists():
        return {}

    mtime = budget_file.stat().st_mtime
    if _route_budget_cache is not None and _route_budget_cache[0] == mtime:
        return _route_budget_cache[1]

    try:
        result = json.loads(budget_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    _route_budget_cache = (mtime, result)
    return result


def check_route_budget(route: str) -> tuple[bool, str]:
    """Check per-route daily budget. Returns (allowed, reason)."""
    if route == "heartbeat":
        return True, ""
    budgets = load_route_budgets()
    bucket = BUDGET_BUCKETS.get(route, "workhorse")
    cap = budgets.get(bucket, {}).get("daily_cap_usd", 0)
    if cap <= 0:
        return True, ""

    # Sum today's spend for this bucket from usage log
    today = datetime.now().strftime("%Y-%m-%d")
    bucket_spend = 0.0
    if USAGE_FILE.exists():
        try:
            with USAGE_FILE.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = entry.get("timestamp", "")
                    if isinstance(ts, str) and ts.startswith(today) and entry.get("bucket") == bucket:
                        try:
                            bucket_spend += float(entry.get("usd", 0))
                        except (TypeError, ValueError):
                            continue
        except OSError:
            pass

    if bucket_spend >= cap:
        return False, f"route_budget_exceeded:{bucket}={bucket_spend:.2f}/{cap:.2f}"
    return True, ""


# ── 24h dedup cache (writing / analysis / research only) ─────────────────────
# 2026-07-16 economics layer: an identical call that succeeded within 24h
# replays the stored text instead of burning a live call. Judgment routes
# (strategy/review/coding) and heartbeat are excluded on purpose — never trade
# freshness on decisions. One JSON file per key under
# DATA_ROOT/operations/llm-dedup/, written via tmp+atomic-rename so concurrent
# daemon workers can never tear a read (worst case both miss and both pay).
DEDUP_ROUTES = frozenset({"writing", "analysis", "research"})
DEDUP_DIR = DATA_ROOT / "operations" / "llm-dedup"
DEDUP_TTL_SECS = 24 * 60 * 60
_dedup_prune_lock = threading.Lock()
_dedup_last_prune = 0.0


def _dedup_key(route: str, prompt: str, provider: str, model: str, effort: str | None) -> str:
    """sha256 over everything that changes what a call would return."""
    material = json.dumps(
        {
            "route": route,
            "system": system_prompt(route),
            "prompt": prompt,
            "provider": provider,
            "model": model,
            "effort": effort or ROUTE_REASONING_EFFORT.get(route),
            "max_output_tokens": ROUTE_MAX_OUTPUT_TOKENS.get(route),
        },
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _dedup_lookup(key: str) -> dict[str, Any] | None:
    try:
        entry = json.loads((DEDUP_DIR / f"{key}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(entry, dict):
        return None
    age = _seconds_since(str(entry.get("ts", "")))
    if age is None or age > DEDUP_TTL_SECS:
        return None
    text = entry.get("text")
    if not (isinstance(text, str) and text.strip()):
        return None
    return entry


def _dedup_store(key: str, result: GenerationResult) -> None:
    try:
        DEDUP_DIR.mkdir(parents=True, exist_ok=True)
        tmp = DEDUP_DIR / f".{key}.{os.getpid()}.tmp"
        tmp.write_text(
            json.dumps(
                {
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "route": result.route,
                    "provider": result.provider,
                    "model": result.model,
                    "runner": result.runner,
                    "text": result.content,
                }
            ),
            encoding="utf-8",
        )
        tmp.replace(DEDUP_DIR / f"{key}.json")
    except OSError as exc:
        _LOGGER.warning("dedup-cache store failed: %s", exc)
    _dedup_prune()


def _dedup_prune() -> None:
    """Unlink expired entries; runs at most once per hour per process."""
    global _dedup_last_prune  # noqa: PLW0603
    with _dedup_prune_lock:
        now = time.monotonic()
        if _dedup_last_prune and now - _dedup_last_prune < 3600:
            return
        _dedup_last_prune = now
    cutoff = time.time() - DEDUP_TTL_SECS
    try:
        for path in DEDUP_DIR.glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue
    except OSError:
        pass


def generate_text(route: str, prompt: str, fallback: str, *, effort: str | None = None, force_fresh: bool = False) -> GenerationResult:
    """Route-aware generation.

    effort: optional per-call reasoning-effort override for OpenAI rungs
    (e.g. "xhigh" on the ≥$499 Managed deal branch). None → route default
    from ROUTE_REASONING_EFFORT.
    force_fresh: bypass the 24h dedup cache (writing/analysis/research) and
    always make a live call.
    """
    _ensure_model_ids_validated()
    provider = resolve_provider(route)
    model = resolve_model(route)

    # Dedup check runs BEFORE budget gates: a hit costs $0, and replaying a
    # real answer beats returning canned fallback text under a budget block.
    dedup_key: str | None = None
    if route in DEDUP_ROUTES and not force_fresh:
        dedup_key = _dedup_key(route, prompt, provider, model, effort)
        cached = _dedup_lookup(dedup_key)
        if cached is not None:
            return finalize(
                route,
                str(cached.get("model", model)),
                "dedup-cache",
                "cached",
                prompt,
                str(cached.get("text", "")),
                str(cached.get("provider", provider)),
                usage=UsageStats(),  # all-zero tokens → usd=0 in the ledger row
                notes=[f"dedup_cache_hit={dedup_key[:16]}"],
            )

    allowed, spent = check_daily_budget(route)
    if not allowed:
        if route in FAIL_LOUD_ROUTES:
            # Fail loud: canned strategy/review/coding output is
            # indistinguishable from real output downstream.
            raise BudgetExceeded(
                f"daily LLM cap blocked {route} call: spent=${spent:.2f} cap=${_get_daily_cap():.2f}"
            )
        return finalize(
            route, model, "budget-cap", "fallback", prompt, fallback, provider,
            notes=[f"daily_cap_exceeded_usd={spent:.2f}"],
        )

    route_allowed, route_reason = check_route_budget(route)
    if not route_allowed:
        if route in FAIL_LOUD_ROUTES:
            raise BudgetExceeded(f"route budget blocked {route} call: {route_reason}")
        return finalize(
            route, model, "route-budget-cap", "fallback", prompt, fallback, provider,
            notes=[route_reason],
        )

    if route == "strategy" and strategy_panel_enabled():
        # Pre-check: panels cost ~4x a single call. If budget is tight, skip panel.
        spent = daily_spend_usd()
        cap = _get_daily_cap()
        # TIER-0 #7 (2026-04-23) — explicit per-day strategy-panel ceiling on
        # top of the global cap. Loaded from config/workflow-budgets.json
        # (_strategy_panel_daily_usd_cap, default $15). Override:
        # RICK_STRATEGY_PANEL_FORCE=1 or RICK_STRATEGY_PANEL_DAILY_CAP_USD=N.
        # Reason: agent finding panel was the biggest $/insight waster at
        # ~$25/call × multiple/day with no quality measurement.
        try:
            panel_cap_env = os.getenv("RICK_STRATEGY_PANEL_DAILY_CAP_USD", "").strip()
            if panel_cap_env:
                panel_cap = float(panel_cap_env)
            else:
                # Lazy-import to avoid circular deps + keep this hot-path cheap.
                import json as _json, pathlib as _pl
                _bp = _pl.Path(os.getenv("RICK_WORKFLOW_BUDGETS_FILE",
                              str(_pl.Path(__file__).resolve().parent.parent / "config" / "workflow-budgets.json")))
                if _bp.exists():
                    panel_cap = float(_json.loads(_bp.read_text(encoding="utf-8")).get("_strategy_panel_daily_usd_cap", 15.0))
                else:
                    panel_cap = 15.0
        except Exception:  # noqa: BLE001 — fail open to default cap
            panel_cap = 15.0
        panel_force = os.getenv("RICK_STRATEGY_PANEL_FORCE", "").strip().lower() in ("1", "true", "yes")
        # Compute today's strategy-panel spend specifically (daily_spend_usd is
        # all routes; we want the strategy slice). Conservative: assume each
        # historical strategy call = panel call (overcounts by ~25% — that's
        # fine for a hard ceiling).
        panel_spent_today = 0.0
        try:
            from runtime.db import connect as _conn
            _c = _conn()
            row = _c.execute(
                "SELECT COALESCE(SUM(cost_usd),0) FROM outcomes "
                "WHERE route = 'strategy' AND created_at >= datetime('now','start of day')"
            ).fetchone()
            panel_spent_today = float(row[0]) if row else 0.0
            _c.close()
        except Exception:  # noqa: BLE001
            pass
        panel_blocked_by_cap = (not panel_force) and (panel_cap > 0) and (panel_spent_today >= panel_cap)

        if cap > 0 and spent >= cap * 0.95:
            # Global daily cap nearly hit — degrade to single-model strategy
            pass
        elif panel_blocked_by_cap:
            # Per-panel daily cap hit — degrade. Telegram alert via finalize notes.
            pass
        elif all(_provider_cooling(p) for p, _ in strategy_panel_refs()):
            # Every panel provider cooling — degrade to the single-model chain,
            # which keeps the claude-cli rung reachable instead of canned text.
            pass
        else:
            return generate_strategy_panel(route, prompt, fallback, effort=effort)

    result = generate_route_with_fallbacks(route, prompt, fallback, provider, model, effort=effort)
    if dedup_key is not None and result.mode == "live" and result.content.strip():
        _dedup_store(dedup_key, result)
    return result
