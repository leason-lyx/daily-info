import json
import shutil
import subprocess
import tempfile
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx

from app.config import Settings
from app.models import Item


TEMPLATES: dict[str, list[str]] = {
    "paper": ["one_sentence", "research_question", "method", "key_results", "limitations", "why_it_matters"],
    "blog": ["one_sentence", "what_happened", "key_takeaways", "who_should_read", "caveats", "why_it_matters"],
    "post": ["one_sentence", "main_update_or_claim", "context", "signal_type", "subjectivity_notice", "why_it_matters"],
}


def content_hash(item: Item) -> str:
    return sha256(f"{item.title}\n{item.raw_text[:8000]}".encode("utf-8")).hexdigest()


def build_prompt(item: Item) -> str:
    keys = TEMPLATES.get(item.content_type, TEMPLATES["blog"])
    raw_text = item.raw_text.strip()
    if not raw_text:
        raise ValueError("raw_text is empty; cannot summarize from original text")
    return (
        "You summarize research intelligence for a Chinese AI researcher. "
        "Return strict JSON only, using these keys: "
        + ", ".join(keys)
        + ". Fields named key_results or key_takeaways must be JSON arrays of Chinese strings. "
        "All other fields must be Chinese strings.\n\n"
        f"Title: {item.title}\n"
        f"Source: {item.source_name}\n"
        f"Type: {item.content_type}\n"
        f"Feed summary for context only: {item.summary[:1200]}\n"
        f"Original text to summarize:\n{raw_text[:12000]}"
    )


def validate_summary(data: Any, content_type: str) -> dict[str, Any]:
    keys = TEMPLATES.get(content_type, TEMPLATES["blog"])
    if not isinstance(data, dict):
        raise ValueError("summary output is not a JSON object")
    normalized: dict[str, Any] = {}
    for key in keys:
        value = data.get(key, [] if key in {"key_results", "key_takeaways"} else "")
        if key in {"key_results", "key_takeaways"}:
            if isinstance(value, list):
                normalized[key] = [str(item) for item in value]
            elif value:
                normalized[key] = [str(value)]
            else:
                normalized[key] = []
        else:
            normalized[key] = "" if value is None else str(value)
    return normalized


def int_usage(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def normalize_usage(usage: Any) -> dict[str, Any]:
    if not isinstance(usage, dict):
        usage = {}
    completion_details = usage.get("completion_tokens_details")
    if not isinstance(completion_details, dict):
        completion_details = {}
    prompt_tokens = int_usage(usage.get("prompt_tokens") or usage.get("input_tokens"))
    completion_tokens = int_usage(usage.get("completion_tokens") or usage.get("output_tokens"))
    total_tokens = int_usage(usage.get("total_tokens"))
    if not total_tokens:
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "reasoning_tokens": int_usage(completion_details.get("reasoning_tokens") or usage.get("reasoning_tokens")),
        "raw": usage,
    }


def load_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        data = json.loads(candidate[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("summary output is not a JSON object")
    return data


async def summarize_openai_compatible(item: Item, settings: Settings) -> dict[str, Any]:
    if not (settings.llm_base_url and settings.llm_api_key and settings.llm_model_name):
        raise RuntimeError("OpenAI-compatible provider is not fully configured")
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": settings.llm_model_name,
        "temperature": settings.llm_temperature,
        "messages": [
            {"role": "system", "content": "Return strict JSON only. Write Chinese summaries."},
            {"role": "user", "content": build_prompt(item)},
        ],
        "response_format": {"type": "json_object"},
    }
    started = perf_counter()
    async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
        response = await client.post(url, json=payload, headers={"Authorization": f"Bearer {settings.llm_api_key}"})
    duration_ms = int((perf_counter() - started) * 1000)
    if response.status_code >= 400:
        raise RuntimeError(f"LLM request failed with {response.status_code}: {response.text[:500]}")
    response_data = response.json()
    content = response_data["choices"][0]["message"]["content"]
    return {
        "data": validate_summary(load_json_object(content), item.content_type),
        "usage": normalize_usage(response_data.get("usage")),
        "duration_ms": duration_ms,
    }


async def summarize_codex_cli(item: Item, settings: Settings) -> dict[str, Any]:
    if not shutil.which(settings.codex_cli_path):
        raise RuntimeError(f"Codex CLI not found: {settings.codex_cli_path}")
    with tempfile.NamedTemporaryFile(prefix="daily-info-codex-summary-", suffix=".json", delete=False) as output_file:
        output_path = Path(output_file.name)
    cmd = [
        settings.codex_cli_path,
        "exec",
        "--ephemeral",
        "--color",
        "never",
        "-o",
        str(output_path),
    ]
    if settings.codex_cli_model:
        cmd.extend(["--model", settings.codex_cli_model])
    cmd.append("Return only strict JSON. Summarize the <stdin> item in Chinese using exactly the requested keys.")
    started = perf_counter()
    try:
        result = subprocess.run(
            cmd,
            input=build_prompt(item),
            capture_output=True,
            text=True,
            timeout=settings.llm_timeout,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Codex CLI failed")
        content = output_path.read_text(encoding="utf-8").strip()
        if not content:
            content = result.stdout.strip()
        return {
            "data": validate_summary(load_json_object(content), item.content_type),
            "usage": normalize_usage({}),
            "duration_ms": int((perf_counter() - started) * 1000),
        }
    finally:
        output_path.unlink(missing_ok=True)


async def summarize_item(item: Item, settings: Settings) -> dict[str, Any]:
    if settings.llm_provider_type == "openai_compatible":
        return await summarize_openai_compatible(item, settings)
    if settings.llm_provider_type == "codex_cli":
        return await summarize_codex_cli(item, settings)
    raise RuntimeError("AI provider is not configured")
