"""Talks to the LLM server: Ollama native API or any OpenAI-compatible endpoint.

Streaming yields dicts:
  {"type": "text", "text": "..."}   - content; reasoning is wrapped in <think>...</think>
  {"type": "usage", "in": int, "out": int}
"""
from __future__ import annotations

import json
from contextlib import aclosing
from typing import Any, AsyncIterator

import httpx

TIMEOUT = httpx.Timeout(connect=8.0, read=900.0, write=30.0, pool=10.0)


class LLMError(Exception):
    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


# ------------------------------------------------------------------ helpers
def _is_openai(s: dict) -> bool:
    return s.get("provider") == "openai"


def _base(s: dict) -> str:
    return (s["openai_url"] if _is_openai(s) else s["ollama_url"]).rstrip("/")


def _label(s: dict) -> str:
    return "the OpenAI-compatible server" if _is_openai(s) else "Ollama"


def _headers(s: dict) -> dict:
    h = {"Content-Type": "application/json"}
    if _is_openai(s) and s.get("openai_key"):
        h["Authorization"] = f"Bearer {s['openai_key']}"
    return h


def _unreachable(s: dict) -> LLMError:
    if _is_openai(s):
        hint = (
            "Start your local server (LM Studio / llama.cpp) and check the base URL "
            "in Settings → Models."
        )
    else:
        hint = (
            "Start Ollama (open the Ollama app or run `ollama serve`) and check the "
            "endpoint URL in Settings → Models."
        )
    return LLMError("unreachable", f"Can't reach {_label(s)} at {_base(s)}.", hint)


def _classify(s: dict, model: str, status: int, text: str) -> LLMError:
    msg = text
    try:
        data = json.loads(text)
        err = data.get("error", data)
        msg = err.get("message", err) if isinstance(err, dict) else str(err)
    except (ValueError, AttributeError):
        pass
    msg = str(msg).strip() or f"HTTP {status}"
    low = msg.lower()
    if status == 404 or "not found" in low or "model_not_found" in low:
        if _is_openai(s):
            hint = f"Load the model `{model}` in your server, or pick another model."
        else:
            hint = f"Download it with `ollama pull {model}`, or pick another model."
        return LLMError("model_missing", f"Model “{model}” isn't available.", hint)
    if "xml syntax error" in low or ("closed by" in low and "element" in low) or "unexpected eof" in low:
        # Ollama failed to parse the tool call the model wrote (typically a long/cut-off call)
        return LLMError(
            "bad_tool_call", msg[:300],
            "The model wrote a malformed tool call. Raising the context length in Settings → Models usually helps.",
        )
    if "context" in low and ("length" in low or "exceed" in low):
        return LLMError(
            "context", msg, "Start a new chat or raise the context length in Settings → Models."
        )
    return LLMError("server", msg[:500], "")


class _Think:
    """Wrap separate reasoning chunks into inline <think>...</think> tags."""

    def __init__(self) -> None:
        self.open = False

    def feed(self, thinking: str | None, content: str | None) -> str:
        out: list[str] = []
        if thinking:
            if not self.open:
                out.append("<think>")
                self.open = True
            out.append(thinking)
        if content:
            if self.open:
                out.append("</think>")
                self.open = False
            out.append(content)
        return "".join(out)

    def close(self) -> str:
        if self.open:
            self.open = False
            return "</think>"
        return ""


# --------------------------------------------------------------- list models
async def list_models(s: dict) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(6.0)) as client:
            if _is_openai(s):
                r = await client.get(f"{_base(s)}/models", headers=_headers(s))
            else:
                r = await client.get(f"{_base(s)}/api/tags")
    except httpx.HTTPError:
        raise _unreachable(s)
    if r.status_code != 200:
        raise LLMError("server", f"{_label(s)} answered HTTP {r.status_code}.", r.text[:200])
    data = r.json()
    models: list[dict] = []
    if _is_openai(s):
        for m in data.get("data", []):
            models.append({"name": m["id"], "size": None, "params": "", "family": ""})
    else:
        for m in data.get("models", []):
            name = m.get("name") or m.get("model")
            if not name or "embed" in name.lower():
                continue
            d = m.get("details") or {}
            models.append(
                {
                    "name": name,
                    "size": m.get("size"),
                    "params": d.get("parameter_size", ""),
                    "family": d.get("family", ""),
                }
            )
    models.sort(key=lambda m: m["name"].lower())
    return models


# ------------------------------------------------------------------ streaming
async def stream_chat(
    s: dict,
    model: str,
    messages: list[dict],
    think: bool | None = None,
    tools: list[dict] | None = None,
) -> AsyncIterator[dict]:
    """Extra event: {"type": "tool_calls", "calls": [{"id", "name", "arguments"}]}"""
    gen = (
        _stream_openai(s, model, messages, tools)
        if _is_openai(s)
        else _stream_ollama(s, model, messages, think, tools)
    )
    try:
        async with aclosing(gen) as g:
            async for event in g:
                yield event
    except (httpx.ConnectError, httpx.ConnectTimeout):
        raise _unreachable(s)
    except httpx.ReadTimeout:
        raise LLMError("timeout", "The model took too long to respond.", "Try a smaller model.")
    except httpx.HTTPError as exc:
        raise LLMError("network", f"Network error: {exc.__class__.__name__}", str(exc)[:200])


# Models that rejected the `think` parameter (they don't support reasoning); remembered per run.
_NO_THINK: set[str] = set()
_NO_TOOLS: set[str] = set()  # models that rejected native tool calling


async def _stream_ollama(
    s: dict, model: str, messages: list[dict], think_opt: bool | None, tools: list[dict] | None
) -> AsyncIterator[dict]:
    options: dict[str, Any] = {
        "temperature": s["temperature"],
        "top_p": s["top_p"],
        "num_ctx": s["num_ctx"],
    }
    if s["max_tokens"] and s["max_tokens"] > 0:
        options["num_predict"] = s["max_tokens"]
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": options,
        "keep_alive": s["keep_alive"] or "5m",
    }
    if tools:
        payload["tools"] = tools
    think = _Think()
    calls: list[dict] = []
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for attempt in (0, 1):
            body_payload = dict(payload)
            if think_opt is not None and model not in _NO_THINK:
                body_payload["think"] = think_opt
            async with client.stream("POST", f"{_base(s)}/api/chat", json=body_payload) as r:
                if r.status_code != 200:
                    body = (await r.aread()).decode("utf-8", "replace")
                    if tools and "support tools" in body.lower():
                        _NO_TOOLS.add(model)
                        raise LLMError("no_tools", "This model has no native tool calling.")
                    if attempt == 0 and "think" in body_payload and "think" in body.lower():
                        _NO_THINK.add(model)  # model has no reasoning support: retry without it
                        continue
                    raise _classify(s, model, r.status_code, body)
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    obj = json.loads(line)
                    if "error" in obj:
                        raise _classify(s, model, 500, json.dumps(obj))
                    msg = obj.get("message") or {}
                    text = think.feed(msg.get("thinking"), msg.get("content"))
                    if text:
                        yield {"type": "text", "text": text}
                    for tc in msg.get("tool_calls") or []:
                        fn = tc.get("function") or {}
                        args = fn.get("arguments") or {}
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except ValueError:
                                args = {}
                        calls.append({"id": f"call_{len(calls)}", "name": fn.get("name", ""), "arguments": args})
                    if obj.get("done"):
                        tail = think.close()
                        if tail:
                            yield {"type": "text", "text": tail}
                        if calls:
                            yield {"type": "tool_calls", "calls": calls}
                        yield {"type": "finish", "reason": obj.get("done_reason") or "stop"}
                        yield {
                            "type": "usage",
                            "in": obj.get("prompt_eval_count") or 0,
                            "out": obj.get("eval_count") or 0,
                        }
            break
    tail = think.close()
    if tail:
        yield {"type": "text", "text": tail}


async def _stream_openai(
    s: dict, model: str, messages: list[dict], tools: list[dict] | None
) -> AsyncIterator[dict]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": s["temperature"],
        "top_p": s["top_p"],
    }
    if s["max_tokens"] and s["max_tokens"] > 0:
        payload["max_tokens"] = s["max_tokens"]
    if tools:
        payload["tools"] = tools
    think = _Think()
    acc_calls: dict[int, dict] = {}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        async with client.stream(
            "POST", f"{_base(s)}/chat/completions", json=payload, headers=_headers(s)
        ) as r:
            if r.status_code != 200:
                body = (await r.aread()).decode("utf-8", "replace")
                if tools and "tool" in body.lower() and r.status_code in (400, 422, 500):
                    _NO_TOOLS.add(model)
                    raise LLMError("no_tools", "This model has no native tool calling.")
                raise _classify(s, model, r.status_code, body)
            async for line in r.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                obj = json.loads(data)
                if obj.get("error"):
                    raise _classify(s, model, 500, json.dumps(obj))
                usage = obj.get("usage")
                if usage:
                    yield {
                        "type": "usage",
                        "in": usage.get("prompt_tokens") or 0,
                        "out": usage.get("completion_tokens") or 0,
                    }
                for choice in obj.get("choices") or []:
                    if choice.get("finish_reason"):
                        yield {"type": "finish", "reason": choice["finish_reason"]}
                    delta = choice.get("delta") or {}
                    for tc in delta.get("tool_calls") or []:
                        slot = acc_calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
                        slot["id"] = tc.get("id") or slot["id"]
                        fn = tc.get("function") or {}
                        slot["name"] += fn.get("name") or ""
                        slot["args"] += fn.get("arguments") or ""
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    text = think.feed(reasoning, delta.get("content"))
                    if text:
                        yield {"type": "text", "text": text}
    tail = think.close()
    if tail:
        yield {"type": "text", "text": tail}
    if acc_calls:
        out_calls = []
        for i, slot in sorted(acc_calls.items()):
            try:
                args = json.loads(slot["args"] or "{}")
            except ValueError:
                args = {}
            out_calls.append({"id": slot["id"] or f"call_{i}", "name": slot["name"], "arguments": args})
        yield {"type": "tool_calls", "calls": out_calls}


# ------------------------------------------------------- capabilities / messages
_CAPS: dict[tuple[str, str], dict] = {}


async def capabilities(s: dict, model: str) -> dict:
    """{"tools": bool|None, "vision": bool|None}; None means unknown (try and see)."""
    if _is_openai(s):
        return {"tools": False if model in _NO_TOOLS else None, "vision": None}
    key = (_base(s), model)
    if key in _CAPS:
        caps = dict(_CAPS[key])
    else:
        caps = {"tools": None, "vision": None}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(8.0)) as client:
                r = await client.post(f"{_base(s)}/api/show", json={"model": model})
            if r.status_code == 200:
                listed = r.json().get("capabilities")
                if isinstance(listed, list):
                    caps = {"tools": "tools" in listed, "vision": "vision" in listed}
                    _CAPS[key] = dict(caps)
        except httpx.HTTPError:
            pass
    if model in _NO_TOOLS:
        caps["tools"] = False
    return caps


def user_message(s: dict, text: str, images_b64: list[tuple[str, str]]) -> dict:
    """images_b64: [(mime, base64)]"""
    if not images_b64:
        return {"role": "user", "content": text}
    if _is_openai(s):
        parts: list[dict] = [{"type": "text", "text": text}]
        parts += [{"type": "image_url", "image_url": {"url": f"data:{m};base64,{b}"}} for m, b in images_b64]
        return {"role": "user", "content": parts}
    return {"role": "user", "content": text, "images": [b for _, b in images_b64]}


def assistant_tool_message(s: dict, text: str, calls: list[dict]) -> dict:
    if _is_openai(s):
        return {
            "role": "assistant",
            "content": text or None,
            "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                for c in calls
            ],
        }
    return {
        "role": "assistant",
        "content": text,
        "tool_calls": [{"function": {"name": c["name"], "arguments": c["arguments"]}} for c in calls],
    }


def tool_result_message(s: dict, call: dict, content: str) -> dict:
    if _is_openai(s):
        return {"role": "tool", "tool_call_id": call["id"], "content": content}
    return {"role": "tool", "tool_name": call["name"], "content": content}


# --------------------------------------------------------------- non-streaming
async def complete(s: dict, model: str, messages: list[dict], max_tokens: int = 48, json_mode: bool = False,
                   num_ctx: int = 2048, timeout: float = 120.0) -> str:
    """Single short completion (used for chat titles). Reasoning is disabled where possible."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=8.0, read=timeout, write=30.0, pool=10.0)) as client:
            if _is_openai(s):
                r = await client.post(
                    f"{_base(s)}/chat/completions",
                    headers=_headers(s),
                    json={
                        "model": model,
                        "messages": messages,
                        "stream": False,
                        "temperature": 0.3,
                        "max_tokens": max_tokens,
                        **({"response_format": {"type": "json_object"}} if json_mode else {}),
                    },
                )
                if r.status_code != 200:
                    raise _classify(s, model, r.status_code, r.text)
                return r.json()["choices"][0]["message"].get("content") or ""
            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
                "think": False,
                "options": {"temperature": 0.3, "num_predict": max_tokens, "num_ctx": num_ctx},
                "keep_alive": s["keep_alive"] or "5m",
                **({"format": "json"} if json_mode else {}),
            }
            r = await client.post(f"{_base(s)}/api/chat", json=payload)
            if r.status_code == 400:  # older Ollama / model without `think` support
                payload.pop("think")
                r = await client.post(f"{_base(s)}/api/chat", json=payload)
            if r.status_code != 200:
                raise _classify(s, model, r.status_code, r.text)
            return (r.json().get("message") or {}).get("content") or ""
    except httpx.HTTPError:
        raise _unreachable(s)
