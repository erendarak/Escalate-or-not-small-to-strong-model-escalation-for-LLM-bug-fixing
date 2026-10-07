"""Model clients behind one interface.

* OpenAICompatClient - anything speaking the OpenAI chat-completions protocol:
  Ollama (http://localhost:11434/v1), OpenAI, OpenRouter, vLLM, LM Studio...
* FakeClient - deterministic, no network, no cost. For testing the plumbing.

Transport problems raise InfraError so the runner can tell them apart from
bad model output (which is the model's failure and stays in the denominator).
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field


class InfraError(Exception):
    """Connection refused, rate limit, server 5xx... not the model's fault."""


@dataclass
class ModelResponse:
    text: str
    model_id: str
    input_tokens: int | None
    output_tokens: int | None
    latency_s: float
    cost_usd: float
    raw_usage: dict = field(default_factory=dict)


@dataclass
class ModelSpec:
    key: str                 # name used in configs/policies, e.g. "small"
    kind: str                # "openai_compat" | "fake"
    model: str               # provider model id, e.g. "qwen2.5-coder:1.5b"
    base_url: str | None = None
    api_key_env: str | None = None
    usd_per_1m_input: float = 0.0
    usd_per_1m_output: float = 0.0
    temperature: float = 0.0
    max_tokens: int = 1500
    extra: dict = field(default_factory=dict)   # e.g. {"seed": 0}
    hosting: str = "local"   # local | api  (for reporting)


def cost_usd(spec: ModelSpec, inp: int | None, out: int | None) -> float:
    return ((inp or 0) * spec.usd_per_1m_input + (out or 0) * spec.usd_per_1m_output) / 1e6


class OpenAICompatClient:
    def __init__(self, spec: ModelSpec, timeout_s: float = 300):
        from openai import OpenAI  # imported lazily so fake runs need no SDK
        key = os.environ.get(spec.api_key_env, "") if spec.api_key_env else "not-needed"
        if spec.api_key_env and not key:
            raise InfraError(f"Environment variable {spec.api_key_env} is not set")
        self.spec = spec
        self.client = OpenAI(base_url=spec.base_url, api_key=key, timeout=timeout_s, max_retries=0)

    def generate(self, messages: list[dict], seed: int | None = None) -> ModelResponse:
        """`seed` (set per repeat by the runner) overrides any seed in spec.extra."""
        import openai
        kwargs = dict(self.spec.extra)
        if seed is not None:
            kwargs["seed"] = seed
        t0 = time.perf_counter()
        try:
            r = self.client.chat.completions.create(
                model=self.spec.model, messages=messages, temperature=self.spec.temperature,
                max_tokens=self.spec.max_tokens, **kwargs)
        except (openai.APIConnectionError, openai.APITimeoutError, openai.RateLimitError,
                openai.InternalServerError) as exc:
            raise InfraError(f"{type(exc).__name__}: {exc}") from exc
        latency = time.perf_counter() - t0
        u = r.usage
        inp = getattr(u, "prompt_tokens", None) if u else None
        out = getattr(u, "completion_tokens", None) if u else None
        return ModelResponse(text=r.choices[0].message.content or "", model_id=r.model or self.spec.model,
                             input_tokens=inp, output_tokens=out, latency_s=latency,
                             cost_usd=cost_usd(self.spec, inp, out),
                             raw_usage=u.model_dump() if u else {})


class FakeClient:
    """Deterministic stand-in for a model.

    behaviour:
      "echo"   - returns the buggy program unchanged (always fails)  -> a 'weak' model
      "oracle" - returns the reference solution (always passes)      -> a 'strong' model
      "junk"   - returns text with no code block (invalid output)

    "oracle" reads the PRIVATE reference. It exists only to prove the pipeline
    works end-to-end; it is never used in a real campaign.
    """

    def __init__(self, spec: ModelSpec, data_dir=None):
        self.spec, self.data_dir = spec, data_dir
        self.behaviour = spec.model
        self.current_task = None  # set by the runner before each call (fake only)

    def generate(self, messages: list[dict], seed: int | None = None) -> ModelResponse:
        from .tasks import load_private, load_public
        if self.behaviour == "echo":
            code = load_public(self.data_dir, self.current_task).buggy_code
            text = f"```python\n{code}\n```"
        elif self.behaviour == "oracle":
            code = load_private(self.data_dir, self.current_task).reference_code
            text = f"```python\n{code}\n```"
        elif self.behaviour == "junk":
            text = "I think the bug is on line 3."
        else:
            raise ValueError(self.behaviour)
        n_in = sum(len(m["content"]) for m in messages) // 4
        n_out = len(text) // 4
        return ModelResponse(text=text, model_id=f"fake-{self.behaviour}", input_tokens=n_in,
                             output_tokens=n_out, latency_s=0.0, cost_usd=cost_usd(self.spec, n_in, n_out))


def make_client(spec: ModelSpec, data_dir=None):
    if spec.kind == "fake":
        return FakeClient(spec, data_dir)
    if spec.kind == "openai_compat":
        return OpenAICompatClient(spec)
    raise ValueError(f"unknown model kind {spec.kind}")
