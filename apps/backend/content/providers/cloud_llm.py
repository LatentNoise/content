"""CloudSummarizer: a cloud LLM runner for the ``text.summarize`` operation.

Supports Anthropic and OpenAI behind one class, selected by ``provider``. It is
``location = "cloud"`` so ``constraints.privacy.allow_cloud_providers: false``
excludes it from planning. It shares the server-known prompt template with the
local Ollama runner (contract D10), so summaries stay consistent across LLMs.

Dependency-free (stdlib ``urllib``); the API key is a secret and never leaves
this process except as the request's auth header.
"""

import json
import time
import urllib.error
import urllib.request

from content.domain.plan import PlanStep
from content.processors.chapters import execute_derive
from content.processors.summarize import (
    build_summary_prompt,
    strip_markdown_fence,
    strip_thinking,
    transcript_text_from_material,
)
from content.processors.translate import execute_translation
from content.providers.base import (
    ExecutionContext,
    ProducedFile,
    StepExecutionError,
)

_ENDPOINTS = {
    "anthropic": "https://api.anthropic.com/v1/messages",
    "openai": "https://api.openai.com/v1/chat/completions",
}

# Stop reasons that mean "this answer is not the whole answer". Returning one of
# these as an artifact is the failure this slice exists to remove: a summary cut
# off mid-sentence, or a refusal rendered as an empty file, both looked like
# success. Anthropic reports them on `stop_reason`, OpenAI on `finish_reason`.
_TRUNCATED = {"max_tokens", "length"}
_REFUSED = {"refusal", "content_filter"}

# Roughly four characters per token — good enough to size a budget, and far
# better than one constant for every operation.
_CHARS_PER_TOKEN = 4

# (floor, ceiling, share of the input) per operation. A translation tracks its
# input almost line for line; a summary is a fraction of it; a chapter list is
# bounded by how many chapters a video can sensibly have, not by its length.
_OUTPUT_BUDGET = {
    "text.summarize": (512, 8192, 0.25),
    "text.translate": (1024, 16384, 1.5),
    "chapters.derive": (512, 4096, 0.05),
}

# Transport blips and rate limits are worth one more try; a long generation that
# actually ran is not — replaying it blindly doubles the cost and the latency.
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = 1.5


class CloudSummarizer:
    location = "cloud"
    operations = ("text.summarize", "text.translate", "chapters.derive")

    def __init__(self, provider: str, api_key: str, model: str):
        if provider not in _ENDPOINTS:
            raise ValueError(f"unknown cloud LLM provider '{provider}'")
        self.name = provider  # "anthropic" | "openai" — the runner/registry key
        self.provider = provider
        self._api_key = api_key
        self.model = model
        self.tool_version = provider

    def available(self) -> bool:
        return bool(self._api_key and self.model)

    def resolve_model(self) -> str:
        return self.model

    def _headers(self) -> dict:
        if self.provider == "anthropic":
            return {
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        return {
            "authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }

    def max_tokens_for(self, operation: str, prompt: str) -> int:
        """An output ceiling proportional to the work.

        One constant could only ever be wrong in both directions: 2000 tokens
        truncates a three-hour transcript's translation and is wasteful on a
        one-paragraph summary. Derived from the operation and the input size,
        clamped so a pathological input cannot ask for an unbounded generation.
        """
        floor, ceiling, share = _OUTPUT_BUDGET.get(
            operation, _OUTPUT_BUDGET["text.summarize"]
        )
        approx_input = max(len(prompt) // _CHARS_PER_TOKEN, 1)
        return int(max(floor, min(approx_input * share, ceiling)))

    def _payload(self, prompt: str, operation: str) -> dict:
        messages = [{"role": "user", "content": prompt}]
        limit = self.max_tokens_for(operation, prompt)
        if self.provider == "anthropic":
            return {"model": self.model, "max_tokens": limit, "messages": messages}
        # OpenAI had no cap at all, which is the opposite failure: a runaway
        # generation billed and timed out rather than truncated.
        return {"model": self.model, "messages": messages, "max_tokens": limit}

    @staticmethod
    def _extract(provider: str, data: dict) -> tuple[str, str]:
        """``(text, stop_reason)``.

        The stop reason travels with the text because the caller cannot judge
        completeness from the text alone — a truncated summary reads perfectly
        well right up to the point where it stops.
        """
        if provider == "anthropic":
            blocks = data.get("content") or []
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            return text, str(data.get("stop_reason") or "")
        choices = data.get("choices") or []
        if not choices:
            return "", ""
        message = choices[0].get("message", {}) or {}
        # Current models may carry an explicit refusal field alongside a null
        # content; treat it as the refusal it is rather than as empty output.
        if message.get("refusal"):
            return "", "refusal"
        return (
            message.get("content") or "",
            str(choices[0].get("finish_reason") or ""),
        )

    def _post(self, prompt: str, operation: str, timeout: float) -> dict:
        """One HTTP call, retried only where a retry is safe and useful."""
        body = json.dumps(self._payload(prompt, operation)).encode()
        last: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            request = urllib.request.Request(
                _ENDPOINTS[self.provider],
                data=body,
                headers=self._headers(),
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:300]
                if exc.code not in _RETRY_STATUSES or attempt == _MAX_ATTEMPTS - 1:
                    # A 4xx that is not a rate limit is a request we got wrong;
                    # repeating it verbatim would fail identically.
                    raise StepExecutionError(
                        "provider_error",
                        f"{self.provider} API error {exc.code}: {detail}",
                    ) from exc
                last = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == _MAX_ATTEMPTS - 1:
                    raise StepExecutionError(
                        "provider_error", f"{self.provider} unreachable: {exc}"
                    ) from exc
                last = exc
            time.sleep(_BACKOFF_SECONDS * (attempt + 1))
        raise StepExecutionError(
            "provider_error", f"{self.provider} unreachable: {last}"
        )

    def _call(
        self, prompt: str, timeout: float, operation: str = "text.summarize"
    ) -> str:
        data = self._post(prompt, operation, timeout)
        text, stop_reason = self._extract(self.provider, data)
        if stop_reason in _TRUNCATED:
            raise StepExecutionError(
                "output_truncated",
                (
                    f"{self.provider} stopped at the output limit "
                    f"({self.max_tokens_for(operation, prompt)} tokens), so the "
                    "result is incomplete. It was not written as an artifact."
                ),
                details={"stop_reason": stop_reason, "operation": operation},
            )
        if stop_reason in _REFUSED:
            raise StepExecutionError(
                "provider_refused",
                f"{self.provider} declined to answer (stop reason "
                f"'{stop_reason}'), so no artifact was produced.",
                details={"stop_reason": stop_reason},
            )
        return strip_thinking(text)

    def execute(self, step: PlanStep, ctx: ExecutionContext) -> list[ProducedFile]:
        if step.operation in ("text.translate", "chapters.derive"):
            timeout = float(ctx.timeout_seconds)
            if step.operation == "chapters.derive":
                ctx.on_progress(5.0, f"chaptering with {self.provider}/{self.model}")
                return execute_derive(
                    step,
                    ctx,
                    lambda prompt: self._call(prompt, timeout, "chapters.derive"),
                    self.model,
                )
            ctx.on_progress(5.0, f"translating with {self.provider}/{self.model}")
            return execute_translation(
                step,
                ctx,
                lambda prompt: self._call(prompt, timeout, "text.translate"),
                self.model,
            )
        if step.operation != "text.summarize":
            raise StepExecutionError(
                "operation_not_supported",
                f"Provider '{self.name}' cannot execute '{step.operation}'.",
            )
        material = next(
            (
                m
                for m in ctx.input_materials
                if m.path.suffix.lower() in (".json", ".txt")
            ),
            None,
        )
        if material is None:
            raise StepExecutionError(
                "no_input", "No transcript material was produced by the dependency."
            )
        text = transcript_text_from_material(material)
        if not text:
            raise StepExecutionError("no_input", "The transcript is empty.")

        prompt = build_summary_prompt(
            text=text,
            language=step.params.get("language", "auto"),
            length=step.params.get("length", "medium"),
            style=step.params.get("style", "structured"),
            output_format=step.params.get("format", "markdown"),
        )
        ctx.on_progress(5.0, f"summarizing with {self.provider}/{self.model}")
        summary = self._call(
            prompt, timeout=float(ctx.timeout_seconds), operation="text.summarize"
        )
        if not summary.strip():
            raise StepExecutionError(
                "no_output", "The model returned an empty summary."
            )

        suffix = (
            ".md" if step.params.get("format", "markdown") == "markdown" else ".txt"
        )
        path = ctx.workdir / f"summary-{step.id}{suffix}"
        # Models wrap Markdown answers in a fence; it is not part of the
        # summary and would be rendered as code downstream.
        path.write_text(strip_markdown_fence(summary) + "\n")
        return [
            ProducedFile(
                path=path,
                media_type="text/markdown" if suffix == ".md" else "text/plain",
                attributes={
                    "model": f"{self.provider}/{self.model}",
                    "source_language": material.attributes.get("language", ""),
                    "derived_from": "transcript",
                },
            )
        ]
