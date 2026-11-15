"""Thin async LLM client for the provider's protocols: /chat/completions, /messages, /responses.

No vendor SDKs: we control every header (the provider wants our own User-Agent and a stable
`x-opencode-session` per case) and read token usage the same way for every model.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from mikiwame import USER_AGENT
from mikiwame.llm.registry import NO_THINK, ModelRef

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImagePart:
    data: bytes
    media_type: str = "image/jpeg"

    def b64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")


@dataclass
class LLMRequest:
    system: str
    user_text: str
    images: list[ImagePart] = field(default_factory=list)
    max_tokens: int = 1500
    # When False, the model's known no-reasoning knob is sent (see registry.NO_THINK).
    think: bool = False
    # With think: the reasoning effort to ask for ("low", "medium"…); None = the model's default.
    effort: str | None = None
    temperature: float = 0.0


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


@dataclass(frozen=True)
class LLMResult:
    text: str
    usage: Usage
    cost_usd: float
    latency_ms: int
    ref: ModelRef
    # Set by LLMRouter.call(parse=...): the validated answer.
    parsed: Any = None


@dataclass(frozen=True)
class CallRecord:
    """What gets logged for every attempt, successful or not."""

    ref: ModelRef
    session_id: str
    usage: Usage
    cost_usd: float
    latency_ms: int
    ok: bool
    error: str | None = None


class LLMError(Exception):
    pass


class QuotaExceeded(LLMError):
    """The provider refused for usage limits. Callers should switch to the fallback model."""


class BadResponse(LLMError):
    pass


class EmptyOutput(LLMError):
    """200 with no answer text. Not retried with the same model."""


Recorder = Callable[[CallRecord], Awaitable[None] | None]

_RETRY_STATUS = {408, 409, 425, 500, 502, 503, 504, 529}


class LLMClient:
    def __init__(
        self,
        *,
        timeout_s: float = 90.0,
        http: httpx.AsyncClient | None = None,
        recorder: Recorder | None = None,
        max_attempts: int = 3,
        backoff_s: float = 2.0,
        env: dict[str, str] | None = None,
    ) -> None:
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=15.0))
        self._recorder = recorder
        self._max_attempts = max_attempts
        self._backoff_s = backoff_s
        self._env = env if env is not None else os.environ
        # (model, think) whose upstream rejected the reasoning knob. The provider routes one
        # model id to several backends and not all accept it (glm-5.3-flash, 2026-09-30), so we
        # drop it.
        self._knob_rejected: set[tuple[str, bool]] = set()

    async def aclose(self) -> None:
        await self._http.aclose()

    def _endpoint(self, ref: ModelRef) -> tuple[str, dict[str, str]]:
        base = self._env.get(ref.provider.base_url_env, "").rstrip("/")
        key = self._env.get(ref.provider.api_key_env, "")
        if not base or not key:
            raise LLMError(f"{ref.provider.name}: missing base URL or API key in environment")
        headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
        if ref.model.protocol == "messages":
            headers |= {"x-api-key": key, "anthropic-version": "2023-06-01"}
            return f"{base}/messages", headers
        headers["Authorization"] = f"Bearer {key}"
        if ref.model.protocol == "responses":
            return f"{base}/responses", headers
        return f"{base}/chat/completions", headers

    @staticmethod
    def build_body(ref: ModelRef, req: LLMRequest, *, knob: bool = True) -> dict:
        extra = _knob(ref, req) if knob else {}
        if ref.model.protocol == "messages":
            content: list[dict] = [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": im.media_type, "data": im.b64()},
                }
                for im in req.images
            ]
            content.append({"type": "text", "text": req.user_text})
            return {
                "model": ref.model.id,
                "system": req.system,
                "messages": [{"role": "user", "content": content}],
                "max_tokens": req.max_tokens,
                "temperature": req.temperature,
                **extra,
            }
        if ref.model.protocol == "responses":
            parts: list[dict] = [
                {"type": "input_image", "image_url": f"data:{im.media_type};base64,{im.b64()}"}
                for im in req.images
            ]
            parts.append({"type": "input_text", "text": req.user_text})
            # No temperature: GPT Luna answers 400 "Unsupported parameter" (2026-10-06). Nothing
            # is stored upstream for a later turn (store=false): each call stands alone.
            return {
                "model": ref.model.id,
                "input": [
                    {"role": "system", "content": req.system},
                    {"role": "user", "content": parts},
                ],
                "max_output_tokens": req.max_tokens,
                "store": False,
                **extra,
            }
        user: str | list[dict]
        if req.images:
            user = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{im.media_type};base64,{im.b64()}"},
                }
                for im in req.images
            ]
            user.append({"type": "text", "text": req.user_text})
        else:
            user = req.user_text
        return {
            "model": ref.model.id,
            "messages": [
                {"role": "system", "content": req.system},
                {"role": "user", "content": user},
            ],
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            **extra,
        }

    @staticmethod
    def parse_response(ref: ModelRef, data: dict) -> tuple[str, Usage]:
        try:
            if ref.model.protocol == "messages":
                text = "".join(
                    b.get("text", "") for b in data["content"] if b.get("type") == "text"
                )
                u = data.get("usage") or {}
                usage = Usage(
                    input_tokens=int(u.get("input_tokens", 0))
                    + int(u.get("cache_read_input_tokens", 0) or 0)
                    + int(u.get("cache_creation_input_tokens", 0) or 0),
                    output_tokens=int(u.get("output_tokens", 0)),
                    cached_tokens=int(u.get("cache_read_input_tokens", 0) or 0),
                )
            elif ref.model.protocol == "responses":
                # Reasoning items come first and carry no text; an answer cut by
                # max_output_tokens has status "incomplete" and may have no message at all.
                text = "".join(
                    c.get("text", "")
                    for item in data["output"]
                    if item.get("type") == "message"
                    for c in item.get("content") or []
                    if c.get("type") == "output_text"
                )
                u = data.get("usage") or {}
                details = u.get("input_tokens_details") or {}
                usage = Usage(
                    input_tokens=int(u.get("input_tokens", 0)),
                    output_tokens=int(u.get("output_tokens", 0)),
                    cached_tokens=int(details.get("cached_tokens", 0) or 0),
                )
            else:
                msg = data["choices"][0]["message"]
                content = msg.get("content") or ""
                if isinstance(content, list):
                    content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
                text = content
                u = data.get("usage") or {}
                details = u.get("prompt_tokens_details") or {}
                usage = Usage(
                    input_tokens=int(u.get("prompt_tokens", 0)),
                    output_tokens=int(u.get("completion_tokens", 0)),
                    cached_tokens=int(details.get("cached_tokens", 0) or 0),
                )
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise BadResponse(f"unexpected response shape: {e!r}") from e
        return text, usage

    async def _record(self, rec: CallRecord, recorder: Recorder | None) -> None:
        recorder = recorder or self._recorder
        if recorder is None:
            return
        try:
            res = recorder(rec)
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            log.exception("failed to record LLM call")

    async def complete(
        self,
        ref: ModelRef,
        req: LLMRequest,
        *,
        session_id: str,
        recorder: Recorder | None = None,
    ) -> LLMResult:
        url, headers = self._endpoint(ref)
        headers["x-opencode-session"] = session_id
        rejected = (ref.model.id, req.think)
        knob = bool(_knob(ref, req)) and rejected not in self._knob_rejected
        body = self.build_body(ref, req, knob=knob)
        last_error: Exception | None = None
        attempt = 0
        while attempt < self._max_attempts:
            attempt += 1
            t0 = time.monotonic()
            error: str | None = None
            try:
                resp = await self._http.post(url, json=body, headers=headers)
                latency_ms = int((time.monotonic() - t0) * 1000)
                if resp.status_code == 200:
                    text, usage = self.parse_response(ref, resp.json())
                    cost = ref.model.cost_usd(
                        usage.input_tokens, usage.output_tokens, usage.cached_tokens
                    )
                    if not text.strip():
                        # Typically reasoning ate max_tokens. Billed, so record the cost; the
                        # same request would fail the same way, so hand over to the fallback.
                        error = f"empty output after {usage.output_tokens} output tokens"
                        await self._record(
                            CallRecord(ref, session_id, usage, cost, latency_ms, False, error),
                            recorder,
                        )
                        raise EmptyOutput(error)
                    await self._record(
                        CallRecord(ref, session_id, usage, cost, latency_ms, ok=True),
                        recorder,
                    )
                    return LLMResult(text, usage, cost, latency_ms, ref)
                error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                if knob and resp.status_code == 400 and _mentions_knob(ref, req, resp.text):
                    log.info("%s rejected the reasoning knob; retrying without it", ref.label)
                    self._knob_rejected.add(rejected)
                    knob = False
                    body = self.build_body(ref, req, knob=False)
                    last_error = LLMError(error)
                    await self._record(
                        CallRecord(
                            ref, session_id, Usage(), 0.0, latency_ms, ok=False, error=error
                        ),
                        recorder,
                    )
                    attempt -= 1  # a free retry: the request itself was wrong, not the link
                    continue
                if resp.status_code in _RETRY_STATUS:
                    last_error = LLMError(error)
                else:
                    await self._record(
                        CallRecord(
                            ref, session_id, Usage(), 0.0, latency_ms, ok=False, error=error
                        ),
                        recorder,
                    )
                    # Usage limits do not clear in seconds: hand over to the fallback model.
                    if resp.status_code in (402, 429):
                        raise QuotaExceeded(error)
                    raise LLMError(error)
            except httpx.TransportError as e:
                latency_ms = int((time.monotonic() - t0) * 1000)
                error = f"{type(e).__name__}: {e}"
                last_error = LLMError(error)
            except (BadResponse, json.JSONDecodeError) as e:
                latency_ms = int((time.monotonic() - t0) * 1000)
                error = f"bad response: {e}"
                last_error = BadResponse(error)
            await self._record(
                CallRecord(ref, session_id, Usage(), 0.0, latency_ms, ok=False, error=error),
                recorder,
            )
            log.warning(
                "%s attempt %d/%d failed: %s", ref.label, attempt, self._max_attempts, error
            )
            if attempt < self._max_attempts:
                await asyncio.sleep(
                    self._backoff_s * (3 ** (attempt - 1)) * random.uniform(0.8, 1.2)
                )
        assert last_error is not None
        raise last_error


def _knob(ref: ModelRef, req: LLMRequest) -> dict[str, object]:
    """Extra body fields about reasoning: off (registry.NO_THINK), or the effort asked for."""
    if not req.think:
        return NO_THINK.get(ref.model.id, {})
    if req.effort and ref.model.protocol == "responses":
        return {"reasoning": {"effort": req.effort}}
    return {}


def _mentions_knob(ref: ModelRef, req: LLMRequest, body: str) -> bool:
    return any(key in body for key in _knob(ref, req))


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str, *, require: str | None = None) -> dict:
    """Parse the JSON object in a model answer.

    Order matters (review 2026-10-01): the whole answer first, then the first object that
    decodes from the first "{", and fenced blocks last. Trying fences first let hostile text
    that contains ``` (copied verbatim into a transcription) replace the real answer with a
    fragment and silently drop every message. With `require`, an object without that key is
    rejected, so a stray fragment can never pass for an answer.
    """

    def ok(obj: object) -> bool:
        return isinstance(obj, dict) and (require is None or require in obj)

    stripped = text.strip()
    try:
        obj = json.loads(stripped)
        if ok(obj):
            return obj
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    start = stripped.find("{")
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(stripped, start)
            if ok(obj):
                return obj
        except json.JSONDecodeError:
            pass
        start = stripped.find("{", start + 1)
    for m in _FENCE.finditer(stripped):
        try:
            obj = json.loads(m.group(1).strip())
            if ok(obj):
                return obj
        except json.JSONDecodeError:
            pass
    # Never echo the text: it is third-party content and ends up in logs and cases.error.
    raise BadResponse(f"no usable JSON object in model output ({len(text)} chars)")
