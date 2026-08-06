"""Claude client for the comprehension passes.

Uses the official `anthropic` SDK rather than the raw-HTTP pattern the digest's
Gemini/Claude overview calls use (daily_digest._gemini_generate,
_call_claude_summary). The SDK already provides what those hand-roll: automatic
retry with exponential backoff on 408/409/429/5xx, typed exceptions, and
guaranteed-valid JSON via output_config.format. What it does not provide, and
what this module adds, is a full-result cache and a per-run circuit breaker.

Model/param notes for the current models (claude-sonnet-5 / claude-opus-5):
  * `temperature`, `top_p`, `top_k` are rejected with a 400 - steer by prompt.
  * `thinking.budget_tokens` is rejected; adaptive thinking is the default.
    Depth is controlled by output_config.effort instead.
  * `max_tokens` caps thinking AND response text together, so leave headroom.
  * A request can come back with stop_reason == "refusal" and empty content;
    check it before reading blocks.
"""

import hashlib
import json

from .paths import CACHE_FILE
from .store import load_json, write_json

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False
    anthropic = None

# Bump when a prompt, schema, or generation param changes so stale cached
# results are not reused (mirrors daily_digest.OVERVIEW_CACHE_VERSION).
COMPREHENSION_CACHE_VERSION = "v1"

# Daily passes (triage, per-item context, thread narrative) run on Sonnet;
# the weekly synthesis essay runs on Opus.
DEFAULT_DAILY_MODEL = "claude-sonnet-5"
DEFAULT_WEEKLY_MODEL = "claude-opus-5"

DEFAULT_MAX_TOKENS = 16000
DEFAULT_EFFORT = "medium"
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

# The SDK retries 408/409/429/5xx itself; this is just a higher ceiling than its
# default of 2. The breaker is for hard failures the SDK has already given up on.
MAX_RETRIES = 4
FAILURE_LIMIT = 3


class ClaudeClient:
    """Cached, breaker-guarded wrapper around messages.create.

    Pass `cache={}` (or a dict) to keep a run entirely in memory; pass
    `cache_path=` to point at a different cache file. Both make this testable
    without network or disk.
    """

    def __init__(
        self,
        model=DEFAULT_DAILY_MODEL,
        cache=None,
        cache_path=CACHE_FILE,
        effort=DEFAULT_EFFORT,
        max_retries=MAX_RETRIES,
        failure_limit=FAILURE_LIMIT,
        verbose=True,
        client=None,
    ):
        self.model = model
        self.effort = effort if effort in EFFORT_LEVELS else DEFAULT_EFFORT
        self.max_retries = max_retries
        self.failure_limit = failure_limit
        self.verbose = verbose
        self.cache_path = cache_path
        self._cache = cache if cache is not None else load_json(cache_path, {})
        self._cache_dirty = False
        self._client = client
        self._client_error = None
        self._failures = 0
        self.calls = 0
        self.cache_hits = 0

    # --- lifecycle ---------------------------------------------------------

    @property
    def client(self):
        if self._client is None and self._client_error is None:
            if not HAS_ANTHROPIC:
                self._client_error = "the anthropic package is not installed"
            else:
                try:
                    # Resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or an
                    # `ant auth login` profile, in that order.
                    self._client = anthropic.Anthropic(max_retries=self.max_retries)
                except Exception as e:
                    self._client_error = str(e)
        return self._client

    @property
    def available(self):
        return self.client is not None and not self.tripped

    @property
    def tripped(self):
        return self._failures >= self.failure_limit

    def save_cache(self):
        if self._cache_dirty and self.cache_path is not None:
            write_json(self.cache_path, self._cache)
            self._cache_dirty = False

    # --- calls -------------------------------------------------------------

    def complete(self, prompt, system=None, max_tokens=DEFAULT_MAX_TOKENS, effort=None, schema=None):
        """Return the model's text, or a parsed object when `schema` is given.

        Returns None on any failure (missing package/key, refusal, tripped
        breaker, unparseable JSON) so callers can degrade rather than crash.
        """
        effort = effort if effort in EFFORT_LEVELS else self.effort
        key = self._cache_key(prompt, system, effort, schema)
        if key in self._cache:
            self.cache_hits += 1
            return self._cache[key].get("value")

        if self.tripped:
            self._log(f"skipping call: {self._failures} failures this run")
            return None
        if self.client is None:
            self._log(f"skipping call: {self._client_error}")
            return None

        request = {
            "model": self.model,
            "max_tokens": max_tokens,
            "output_config": {"effort": effort},
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            request["system"] = system
        if schema:
            request["output_config"]["format"] = {"type": "json_schema", "schema": schema}

        try:
            response = self.client.messages.create(**request)
        except Exception as e:
            self._failures += 1
            self._log(f"call failed ({type(e).__name__}): {e}")
            return None

        self.calls += 1
        if getattr(response, "stop_reason", None) == "refusal":
            self._log("request was declined by safety classifiers")
            return None

        text = _first_text(response)
        if not text:
            self._log(f"empty response (stop_reason={getattr(response, 'stop_reason', None)})")
            return None

        value = text
        if schema:
            try:
                value = json.loads(text)
            except json.JSONDecodeError as e:
                self._failures += 1
                self._log(f"response was not valid JSON: {e}")
                return None

        self._failures = 0
        self._cache[key] = {"value": value, "model": self.model}
        self._cache_dirty = True
        return value

    # --- internals ---------------------------------------------------------

    def _cache_key(self, prompt, system, effort, schema):
        payload = json.dumps(
            {
                "version": COMPREHENSION_CACHE_VERSION,
                "model": self.model,
                "effort": effort,
                "system": system or "",
                "prompt": prompt,
                "schema": schema,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _log(self, message):
        if self.verbose:
            print(f"  [Comprehension/{self.model}] {message}")


def _first_text(response):
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "text":
            return (getattr(block, "text", "") or "").strip()
    return ""
