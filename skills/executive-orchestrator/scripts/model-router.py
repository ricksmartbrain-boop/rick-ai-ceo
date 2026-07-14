#!/usr/bin/env python3
"""Resolve Rick model aliases by task."""

from __future__ import annotations

import argparse
import json
import os
import sys


ROUTES = {
    "strategy": {
        "env": "RICK_STRATEGY_PANEL_MODELS",
        "default": "openai:gpt-5.6-sol,anthropic:claude-opus-4-8,google:gemini-3.1-pro-preview",
        "provider": "Multi-provider panel",
        "runner": "parallel panel plus synthesis",
        "why": "High-stakes prioritization, synthesis, and operating decisions.",
    },
    "coding": {
        "env": "RICK_MODEL_OPENAI_CODING",
        "default": "gpt-5.6-sol",
        "provider": "OpenAI",
        "runner": "OpenAI API for hard coding; Codex CLI loops for repo/git throughput",
        "why": "Hard implementation, architecture, multi-file refactors, and debugging.",
    },
    "writing": {
        "env": "RICK_MODEL_ANTHROPIC_WORKHORSE",
        "default": "claude-sonnet-4-6",
        "provider": "Anthropic",
        "runner": "claude or anthropic cli",
        "why": "Newsletters, social adaptation, polished outbound writing.",
    },
    "review": {
        "env": "RICK_MODEL_ANTHROPIC_STRATEGIC",
        "default": "claude-opus-4-8",
        "provider": "Anthropic",
        "runner": "claude or anthropic cli",
        "why": "Long-form critique, quality control, and risk review.",
    },
    "analysis": {
        "env": "RICK_MODEL_GOOGLE_WORKHORSE",
        "default": "gemini-3.1-pro-preview",
        "provider": "Google",
        "runner": "Gemini API or OpenAI-compatible gateway",
        "why": "Large-context analysis and broad-document synthesis.",
    },
    "heartbeat": {
        "env": "RICK_MODEL_GOOGLE_BUDGET",
        "default": "gemini-3.1-flash-lite-preview",
        "provider": "Google",
        "runner": "cheap recurring parser",
        "why": "Frequent ops checks where cost discipline matters.",
    },
    "research": {
        "env": "RICK_MODEL_XAI_RESEARCH",
        "default": "grok-4-latest",
        "provider": "xAI",
        "runner": "xAI API",
        "why": "Web and X research with citations and live search.",
    },
}
ROUTE_FALLBACK_DEFAULTS = {
    "coding": "openai:gpt-5.6-terra,anthropic:claude-opus-4-8,openai:gpt-5.3-codex,anthropic:claude-sonnet-4-6,google:gemini-3.1-pro-preview",
    "writing": "anthropic:claude-opus-4-8,openai:gpt-5.6-terra,google:gemini-3.1-pro-preview",
    "review": "openai:gpt-5.6-sol,openai:gpt-5.6-terra,google:gemini-3.1-pro-preview",
    "analysis": "anthropic:claude-opus-4-8,openai:gpt-5.6-terra",
    "heartbeat": "anthropic:claude-sonnet-4-6,openai:gpt-5.6-luna",
    "research": "openai:gpt-5.6-terra,google:gemini-3.1-pro-preview,anthropic:claude-sonnet-4-6",
}


def resolve_route(task: str) -> dict:
    route = ROUTES[task]
    env_name = route["env"]
    model = os.getenv(env_name, route["default"])
    payload = {
        "task": task,
        "env": env_name,
        "model": model,
        "provider": route["provider"],
        "runner": route["runner"],
        "why": route["why"],
    }
    if task == "strategy":
        payload["strategy_panel_enabled"] = os.getenv("RICK_STRATEGY_PANEL_ENABLED", "1")
        payload["strategy_synthesis"] = os.getenv("RICK_STRATEGY_PANEL_SYNTHESIS_MODEL", "openai:gpt-5.6-sol")
    else:
        fallback_env = f"RICK_ROUTE_{task.upper()}_FALLBACKS"
        fallbacks = os.getenv(fallback_env, ROUTE_FALLBACK_DEFAULTS.get(task, ""))
        payload["fallback_env"] = fallback_env
        payload["fallbacks"] = fallbacks
    return payload


def print_text(route: dict) -> None:
    print(f"task: {route['task']}")
    print(f"env: {route['env']}")
    print(f"model: {route['model']}")
    print(f"provider: {route['provider']}")
    print(f"runner: {route['runner']}")
    print(f"why: {route['why']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve Rick model routing")
    parser.add_argument("--task", choices=sorted(ROUTES), help="Task type to resolve")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--list", action="store_true", help="List all task routes")
    args = parser.parse_args()

    if not args.list and not args.task:
        parser.error("provide --task or --list")

    if args.list:
        routes = [resolve_route(task) for task in sorted(ROUTES)]
        if args.format == "json":
            json.dump(routes, sys.stdout, indent=2)
            print()
        else:
            for route in routes:
                print_text(route)
                print("")
        return 0

    route = resolve_route(args.task)
    if args.format == "json":
        json.dump(route, sys.stdout, indent=2)
        print()
    else:
        print_text(route)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
