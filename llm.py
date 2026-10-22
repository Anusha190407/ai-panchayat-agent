"""Gemini REST client with structured JSON output.

Design goals for a demo that must not break in front of judges:
* No SDK dependency — plain HTTPS with `requests`.
* Model auto-selection: lists the models your key can use and picks the newest
  Flash-Lite / Flash model; skips models that are missing or have no free quota.
* Request-format fallback: tries the current structured-output format first and
  steps down to older formats if the API rejects it.
* Rate-limit aware: honours retry delays, then cools down so the agents switch
  to the offline engine instead of failing.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass

import requests

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
# Preference order if model discovery is unavailable.
DEFAULT_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.8-flash",
    "gemini-flash-lite-latest",
    "gemini-flash-latest",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
]
_EXCLUDE = ("preview", "exp", "tts", "image", "live", "audio", "embedding", "cyber", "thinking", "computer",
            "robotics", "native", "aqa", "learnlm", "gemma")
# Output-format variants, newest first.
FORMATS = ("responseFormat", "responseJsonSchema", "responseMimeType", "promptOnly")
COOLDOWN_SECONDS = 60


class LLMError(Exception):
    """The model could not produce a usable answer for this request."""


class LLMUnavailable(LLMError):
    """Gemini cannot be used right now (no key, bad key, rate limit, network)."""


class _ModelUnusable(Exception):
    pass


class _RateLimited(Exception):
    pass


@dataclass
class LLMResult:
    data: dict
    model: str
    ms: int
    tokens: int


def _model_rank(name: str):
    """Sort key for discovered models: newest Flash-Lite first, then newest Flash."""
    m = re.fullmatch(r"gemini-(\d+(?:\.\d+)?)-flash(-lite)?", name)
    if not m:
        return None
    return float(m.group(1)), bool(m.group(2))


def order_models(names: list[str]) -> list[str]:
    usable = [n for n in names if n.startswith("gemini-") and not any(x in n for x in _EXCLUDE)]
    ranked = [(n, _model_rank(n)) for n in usable]
    lites = sorted([n for n, r in ranked if r and r[1]], key=lambda n: -_model_rank(n)[0])
    flashes = sorted([n for n, r in ranked if r and not r[1]], key=lambda n: -_model_rank(n)[0])
    aliases = [n for n in ("gemini-flash-lite-latest", "gemini-flash-latest") if n in usable]
    ordered: list[str] = []
    # newest lite, newest flash, then the rest
    for group in (lites[:1], flashes[:1], aliases, lites[1:], flashes[1:]):
        for n in group:
            if n not in ordered:
                ordered.append(n)
    return ordered


def _parse_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.S)
        if not m:
            raise LLMError("Gemini did not return JSON")
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError as exc:
            raise LLMError("Gemini returned malformed JSON") from exc
    if not isinstance(obj, dict):
        raise LLMError("Gemini returned JSON that is not an object")
    return obj


def _extract_text(payload: dict) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        reason = (payload.get("promptFeedback") or {}).get("blockReason", "empty response")
        raise LLMError(f"Gemini returned no answer ({reason})")
    first = candidates[0]
    parts = (first.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
    if not text.strip():
        raise LLMError(f"Gemini returned an empty answer (finishReason={first.get('finishReason')})")
    return text


def _error_info(resp: requests.Response) -> tuple[str, float]:
    """(message, retry-after seconds) from a Gemini error response."""
    message, delay = resp.text[:500], 0.0
    try:
        err = resp.json().get("error", {})
        message = err.get("message", message) or message
        for d in err.get("details", []) or []:
            if "RetryInfo" in d.get("@type", ""):
                m = re.match(r"([\d.]+)s", str(d.get("retryDelay", "")))
                if m:
                    delay = float(m.group(1))
    except ValueError:
        pass
    ra = resp.headers.get("Retry-After")
    if not delay and ra and ra.replace(".", "", 1).isdigit():
        delay = float(ra)
    return message, delay


class GeminiClient:
    def __init__(self, api_key: str | None = None, model: str | None = None, base_url: str | None = None,
                 timeout: tuple[float, float] = (10, 60), session: requests.Session | None = None,
                 max_retry_wait: float = 8.0, cooldown_seconds: float = COOLDOWN_SECONDS):
        self.api_key = (api_key if api_key is not None else
                        os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
        self.forced_model = (model if model is not None else os.getenv("GEMINI_MODEL", "")).strip() or None
        self.base_url = (base_url or os.getenv("GEMINI_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.max_retry_wait = max_retry_wait
        self.cooldown_seconds = cooldown_seconds
        self._lock = threading.Lock()
        self._discovered: list[str] | None = None
        self._dead: set[str] = set()
        self._working: str | None = None
        self._format = 0
        self._thinking = True
        self._cooldown_until = 0.0
        self.key_problem = ""
        self.stats = {"calls": 0, "ok": 0, "errors": 0, "tokens": 0, "last_error": "", "last_model": None}

    # ----------------------------------------------------------------- state
    @property
    def configured(self) -> bool:
        return bool(self.api_key) and not self.key_problem

    def available(self) -> bool:
        return self.configured and time.time() >= self._cooldown_until

    def status(self) -> dict:
        if not self.api_key:
            mode, note = "offline", "No GEMINI_API_KEY set — using the offline engine."
        elif self.key_problem:
            mode, note = "offline", self.key_problem
        elif time.time() < self._cooldown_until:
            mode, note = "cooldown", "Gemini rate limit reached — offline engine for a minute."
        else:
            mode, note = "gemini", "Gemini connected."
        return {
            "mode": mode,
            "note": note,
            "model": self._working or self.forced_model or (self._discovered or DEFAULT_MODELS)[0],
            "calls": self.stats["calls"],
            "ok": self.stats["ok"],
            "errors": self.stats["errors"],
            "tokens": self.stats["tokens"],
            "last_error": self.stats["last_error"],
        }

    # ------------------------------------------------------------ discovery
    def _discover(self) -> list[str]:
        if self._discovered is not None:
            return self._discovered
        names: list[str] = []
        try:
            r = self.session.get(f"{self.base_url}/models", params={"pageSize": 1000},
                                 headers={"x-goog-api-key": self.api_key}, timeout=self.timeout)
            if r.status_code == 200:
                for m in r.json().get("models", []):
                    methods = m.get("supportedGenerationMethods") or []
                    if not methods or "generateContent" in methods:
                        names.append(m.get("name", "").split("/")[-1])
            elif r.status_code in (400, 401, 403) and "api key" in r.text.lower():
                self.key_problem = "Gemini API key is not valid — check GEMINI_API_KEY."
        except requests.RequestException:
            pass
        self._discovered = order_models(names) if names else []
        return self._discovered

    def _candidates(self) -> list[str]:
        discovered = self._discover()
        pool = discovered + [m for m in DEFAULT_MODELS if not discovered or m in discovered]
        ordered: list[str] = []
        for m in ([self._working] if self._working else []) + ([self.forced_model] if self.forced_model else []) + pool:
            if m and m not in ordered and m not in self._dead:
                ordered.append(m)
        return ordered[:6]

    # ------------------------------------------------------------- requests
    def _body(self, system: str, parts: list[dict], schema: dict) -> dict:
        fmt = FORMATS[self._format]
        gen: dict = {}
        sys_text = system
        if fmt == "responseFormat":
            gen["responseFormat"] = {"text": {"mimeType": "APPLICATION_JSON", "schema": schema}}
        elif fmt == "responseJsonSchema":
            gen["responseMimeType"] = "application/json"
            gen["responseJsonSchema"] = schema
        elif fmt == "responseMimeType":
            gen["responseMimeType"] = "application/json"
        if fmt in ("responseMimeType", "promptOnly"):
            sys_text += "\n\nReturn ONLY one JSON object (no markdown) that matches this JSON schema:\n" + json.dumps(schema)
        if self._thinking:
            gen["thinkingConfig"] = {"thinkingLevel": "low"}
        body = {
            "systemInstruction": {"parts": [{"text": sys_text}]},
            "contents": [{"role": "user", "parts": parts}],
        }
        if gen:
            body["generationConfig"] = gen
        return body

    def generate_json(self, *, system: str, prompt: str, schema: dict,
                      images: list[tuple[str, str]] | None = None) -> LLMResult:
        """Ask Gemini for one JSON object. images = [(mime_type, base64_data)]."""
        if not self.api_key:
            raise LLMUnavailable("No GEMINI_API_KEY configured")
        if self.key_problem:
            raise LLMUnavailable(self.key_problem)
        if time.time() < self._cooldown_until:
            raise LLMUnavailable("Gemini rate limit — cooling down")
        parts: list[dict] = [{"text": prompt}]
        for mime, data in images or []:
            parts.append({"inlineData": {"mimeType": mime, "data": data}})

        last: Exception | None = None
        for i, model in enumerate(self._candidates()):
            try:
                # only the first model waits out a short rate-limit delay; others are tried at once
                return self._call(model, system, parts, schema, allow_wait=(i == 0))
            except _ModelUnusable as exc:
                self._dead.add(model)
                last = exc
            except _RateLimited as exc:
                last = exc
        if isinstance(last, _RateLimited) or last is None:
            self._cooldown_until = time.time() + self.cooldown_seconds
        msg = str(last) if last else "no Gemini model available for this key"
        self.stats["last_error"] = msg[:300]
        raise LLMUnavailable(msg)

    def _call(self, model: str, system: str, parts: list[dict], schema: dict, allow_wait: bool = True) -> LLMResult:
        url = f"{self.base_url}/models/{model}:generateContent"
        retried_rate = not allow_wait
        dropped_images = False
        for attempt in range(8):
            body = self._body(system, parts, schema)
            started = time.perf_counter()
            try:
                resp = self.session.post(url, json=body, timeout=self.timeout,
                                         headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"})
            except requests.RequestException as exc:
                with self._lock:
                    self.stats["calls"] += 1
                    self.stats["errors"] += 1
                if attempt == 0:
                    time.sleep(1)
                    continue
                raise _RateLimited(f"network error: {exc.__class__.__name__}") from exc
            ms = round((time.perf_counter() - started) * 1000)
            with self._lock:
                self.stats["calls"] += 1

            if resp.status_code == 200:
                payload = resp.json()
                try:
                    data = _parse_json(_extract_text(payload))
                except LLMError:
                    with self._lock:
                        self.stats["errors"] += 1
                    raise
                tokens = int((payload.get("usageMetadata") or {}).get("totalTokenCount", 0) or 0)
                with self._lock:
                    self.stats["ok"] += 1
                    self.stats["tokens"] += tokens
                    self.stats["last_model"] = model
                    self._working = model
                return LLMResult(data=data, model=model, ms=ms, tokens=tokens)

            with self._lock:
                self.stats["errors"] += 1
            message, delay = _error_info(resp)
            low = message.lower()
            self.stats["last_error"] = f"{resp.status_code}: {message[:240]}"

            if "api key" in low and ("not valid" in low or "invalid" in low or "expired" in low):
                self.key_problem = "Gemini API key is not valid — check GEMINI_API_KEY."
                raise LLMUnavailable(self.key_problem)
            if "location is not supported" in low:
                self.key_problem = "Gemini API is not available from this server's region."
                raise LLMUnavailable(self.key_problem)
            if resp.status_code == 400:
                if self._thinking and "thinking" in low:
                    self._thinking = False
                    continue
                if not dropped_images and len(parts) > 1 and ("image" in low or "inline" in low or "mime" in low):
                    parts = parts[:1]
                    dropped_images = True
                    continue
                if self._format < len(FORMATS) - 1:
                    self._format += 1
                    continue
                raise LLMError(f"Gemini rejected the request: {message[:200]}")
            if resp.status_code == 404:
                raise _ModelUnusable(f"{model}: not available")
            if resp.status_code in (401, 403):
                if "permission" in low and "model" not in low:
                    self.key_problem = "Gemini API access denied — enable the Generative Language API for this key."
                    raise LLMUnavailable(self.key_problem)
                raise _ModelUnusable(f"{model}: access denied")
            if resp.status_code == 429:
                if "limit: 0" in low:
                    raise _ModelUnusable(f"{model}: no free-tier quota")
                if not retried_rate and delay <= self.max_retry_wait:
                    retried_rate = True
                    time.sleep(max(1.0, delay))
                    continue
                raise _RateLimited(f"{model}: rate limited")
            if resp.status_code >= 500:
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise _RateLimited(f"{model}: server error {resp.status_code}")
            raise LLMError(f"Gemini error {resp.status_code}: {message[:200]}")
        raise LLMError("Gemini request failed after several attempts")
