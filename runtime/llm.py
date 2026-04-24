#!/usr/bin/env python3
"""Route-aware text generation with direct provider APIs and strategy panels."""

from __future__ import annotations

import contextvars
import json
import os
import shutil
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


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


ROUTES = {
    "strategy": {
        "env": "RICK_MODEL_OPENAI_STRATEGIC",
        "default": "gpt-5.4",
        "provider": "openai",
    },
    "coding": {
        "env": "RICK_MODEL_OPENAI_CODING",
        "default": "gpt-5.3-codex",
        "provider": "openai",
    },
    "writing": {
        "env": "RICK_MODEL_ANTHROPIC_WORKHORSE",
        "default": "claude-sonnet-4-6",
        "provider": "anthropic",
    },
    "review": {
        "env": "RICK_MODEL_ANTHROPIC_STRATEGIC",
        "default": "claude-opus-4-7",
        "provider": "anthropic",
    },
    "analysis": {
        "env": "RICK_MODEL_GOOGLE_WORKHORSE",
        "default": "gemini-3.1-pro-preview",
        "provider": "google",
    },
    "heartbeat": {
        "env": "RICK_MODEL_GOOGLE_BUDGET",
        "default": "gemini-3.1-flash-lite-preview",
        "provider": "google",
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
    "writing": 8192,
    "coding": 16384,
    "strategy": 8192,
    "review": 8192,
    "research": 8192,
}
ROUTE_REASONING_EFFORT: dict[str, str] = {
    "strategy": "high",
    "review": "high",
    "coding": "high",
}
STRATEGY_PANEL_DEFAULTS = (
    ("openai", "RICK_MODEL_OPENAI_STRATEGIC_PRO", "gpt-5.4"),
    ("anthropic", "RICK_MODEL_ANTHROPIC_STRATEGIC", "claude-opus-4-7"),
    ("google", "RICK_MODEL_GOOGLE_WORKHORSE", "gemini-3.1-pro-preview"),
)
ROUTE_FALLBACK_DEFAULTS = {
    "coding": (
        ("google", "gemini-3.1-pro-preview"),
        ("anthropic", "claude-sonnet-4-6"),
        ("openai", "gpt-5.3-codex"),
        ("anthropic", "claude-opus-4-7"),
    ),
    "writing": (
        ("openai", "gpt-5.4"),
        ("google", "gemini-3.1-pro-preview"),
        ("anthropic", "claude-opus-4-7"),
    ),
    "review": (
        ("google", "gemini-3.1-pro-preview"),
        ("openai", "gpt-5.4-mini"),
    ),
    "analysis": (
        ("openai", "gpt-5.4"),
        ("anthropic", "claude-sonnet-4-6"),
    ),
    "heartbeat": (
        ("google", "gemini-3.1-pro-preview"),
        ("anthropic", "claude-sonnet-4-6"),
    ),
    "research": (
        ("google", "gemini-3.1-pro-preview"),
        ("anthropic", "claude-sonnet-4-6"),
    ),
    "strategy": (
        ("anthropic", "claude-sonnet-4-6"),
        ("google", "gemini-3.1-pro-preview"),
    ),
}
STRATEGY_SYNTHESIS_DEFAULT = ("openai", "RICK_STRATEGY_PANEL_SYNTHESIS_MODEL", "openai:gpt-5.4")
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
    return UsageStats(
        input_tokens=int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
        output_tokens=int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0),
        reasoning_tokens=int(details.get("reasoning_tokens", 0) or 0),
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


def call_openai(model: str, route: str, prompt: str) -> LiveCallResult | None:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    base = os.getenv("RICK_OPENAI_API_BASE_URL", "https://api.openai.com").rstrip("/")
    payload: dict[str, Any] = {
        "model": model,
        "input": prompt,
        "instructions": system_prompt(route),
        "max_output_tokens": ROUTE_MAX_OUTPUT_TOKENS.get(route, int(os.getenv("RICK_LLM_MAX_OUTPUT_TOKENS", "4096"))),
    }
    default_effort = ROUTE_REASONING_EFFORT.get(route)
    if default_effort or model.endswith("-pro"):
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

    if infer_provider(model, "") == "anthropic" and shutil.which("claude"):
        result = subprocess.run(
            ["claude", "--print", prompt],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        if result.returncode == 0 and result.stdout.strip():
            return LiveCallResult(text=result.stdout.strip(), runner="claude-cli", provider="anthropic")
    return None


def run_live_generation(provider: str, model: str, route: str, prompt: str) -> LiveCallResult | None:
    if _circuit_is_open(provider):
        from runtime.log import get_logger
        get_logger("rick.llm").warning("Circuit breaker open for %s — skipping", provider)
        return cli_fallback(model, route, prompt)
    try:
        gateway_result = call_gateway(provider, model, route, prompt)
        if gateway_result:
            _circuit_record_success(provider)
            return gateway_result

        result = None
        if provider == "openai":
            result = call_openai(model, route, prompt)
        elif provider == "anthropic":
            result = call_anthropic(model, route, prompt) or cli_fallback(model, route, prompt)
        elif provider == "google":
            result = call_google(model, route, prompt)
        elif provider == "xai":
            result = call_xai(model, route, prompt)

        if result:
            _circuit_record_success(provider)
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
    ):
        _circuit_record_failure(provider)
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
        return 0.0

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
) -> GenerationResult:
    if os.getenv("RICK_LLM_FALLBACK_ONLY", "").strip() == "1":
        return finalize(route, model, "fallback", "fallback", prompt, fallback_text, provider)

    live = run_live_generation(provider, model, route, prompt)
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


def generate_route_with_fallbacks(
    route: str,
    prompt: str,
    fallback: str,
    primary_provider: str,
    primary_model: str,
) -> GenerationResult:
    if os.getenv("RICK_LLM_FALLBACK_ONLY", "").strip() == "1":
        return finalize(route, primary_model, "fallback", "fallback", prompt, fallback, primary_provider)

    notes: list[str] = []
    refs = [(primary_provider, primary_model), *route_fallback_refs(route, primary_provider, primary_model)]
    failed_refs: list[tuple[str, str]] = []

    for index, (provider, model) in enumerate(refs):
        live = run_live_generation(provider, model, route, prompt)
        if live and live.text.strip():
            if index > 0:
                notes.append(f"route_fallback_used={provider}:{model}")
                # Primary failed but a fallback caught it — surface the silent
                # failure so daily ops can grep llm-fallback-events.jsonl.
                _emit_silent_failure_event(route, failed_refs, (primary_provider, primary_model))
            return finalize(route, model, live.runner, "live", prompt, live.text, live.provider, usage=live.usage, notes=notes)
        notes.append(f"live_failed={provider}:{model}")
        failed_refs.append((provider, model))

    # All refs failed — fully silent fallback. Loud log.
    _emit_silent_failure_event(route, failed_refs, (primary_provider, primary_model))
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


def generate_strategy_panel(route: str, prompt: str, fallback: str) -> GenerationResult:
    refs = strategy_panel_refs()
    ordered_results: list[GenerationResult | None] = [None] * len(refs)
    notes: list[str] = []

    with ThreadPoolExecutor(max_workers=max(1, len(refs))) as executor:
        future_map = {
            executor.submit(generate_candidate, route, prompt, provider, model, "", False): (index, provider, model)
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
    synthesis_result = generate_candidate(route, synthesis_prompt, synthesis_provider, synthesis_model, "", False)
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
    """Parse daily LLM spend cap from env. Returns 0 to disable."""
    try:
        return float(os.getenv("RICK_LLM_DAILY_CAP_USD", "500"))
    except (TypeError, ValueError):
        return 500.0


def check_daily_budget(route: str) -> tuple[bool, float]:
    """Return (allowed, current_spend). Heartbeat is always allowed.

    Cap is controlled by RICK_LLM_DAILY_CAP_USD (default: 50, 0 = unlimited).
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


def generate_text(route: str, prompt: str, fallback: str) -> GenerationResult:
    provider = resolve_provider(route)
    model = resolve_model(route)

    allowed, spent = check_daily_budget(route)
    if not allowed:
        return finalize(
            route, model, "budget-cap", "fallback", prompt, fallback, provider,
            notes=[f"daily_cap_exceeded_usd={spent:.2f}"],
        )

    route_allowed, route_reason = check_route_budget(route)
    if not route_allowed:
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
        else:
            return generate_strategy_panel(route, prompt, fallback)

    return generate_route_with_fallbacks(route, prompt, fallback, provider, model)
