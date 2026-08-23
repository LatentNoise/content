"""OllamaProvider: local LLM service runner for ``text.summarize``.

The first *service* runner (HTTP daemon, not a subprocess): availability is a
runtime fact (daemon reachable, at least one model installed) probed lazily
and cached briefly — this is the installation dimension of feasibility the
planner consults. Content processed here never leaves the machine
(``location = "local"``), so ``privacy.allow_cloud_providers: false`` keeps it
eligible.

Model choice: ``CONTENT_OLLAMA_MODEL`` when set, else the first installed
model (sorted — deterministic per installation). The chosen model is recorded
in the plan step params and in the artifact provenance attributes.
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

_AVAILABILITY_TTL_SECONDS = 30.0


# A token is worth at least this many characters of ordinary prose. Used only
# to compute a *lower bound* on the prompt's token count, so the check can
# never invent a truncation it did not see: real text runs nearer 4 characters
# per token, and scripts like Chinese run nearer 1 — which makes this bound
# conservative for Latin text and simply silent for CJK. Missing a warning is
# the acceptable failure here; crying wolf is not.
_CHARS_PER_TOKEN_FLOOR = 8


def _warn_if_truncated(prompt, payload, model, ctx) -> None:
    """Say so when the daemon read less of the prompt than we sent it.

    Measured on the real deployment (Ollama 0.32, gemma3:4b): a 32 400-word
    transcript and a 90 000-word one both came back with
    ``prompt_eval_count`` of exactly 16 387 — the default
    ``OLLAMA_CONTEXT_LENGTH`` of 16 384 plus the chat scaffolding. The prompt is
    **cut, not refused**: the job succeeds, the artifact looks right, and the
    summary covers the beginning of a recording while appearing to cover all of
    it. Nothing in the response says so; ``prompt_eval_count`` is the only
    trace, and until now nothing read it.

    Roughly 12 000 words — about 1 h 20 of speech — is where that starts. Below
    it nothing is wrong, which is why this stays a warning on a successful step
    rather than a failure.
    """
    read = payload.get("prompt_eval_count")
    if not isinstance(read, int) or read <= 0:
        return  # an older daemon, or a field we cannot trust: say nothing
    floor = len(prompt) // _CHARS_PER_TOKEN_FLOOR
    if read >= floor:
        return
    kept = max(1, round(100 * read / floor))
    ctx.on_warning(
        "partial_output",
        (
            f"The model read {read} tokens of a prompt of at least ~{floor}: "
            f"at most {kept}% of the source reached it, and the rest was "
            "silently dropped by the context window. Raise "
            "OLLAMA_CONTEXT_LENGTH on the daemon, or send a shorter source."
        ),
        {
            "provider": "ollama",
            "model": model,
            "prompt_eval_count": read,
            "prompt_tokens_at_least": floor,
        },
    )


class OllamaProvider:
    name = "ollama"
    location = "local"
    operations = ("text.summarize", "text.translate", "chapters.derive")

    def __init__(self, base_url: str = "http://localhost:11434", model: str = ""):
        self.base_url = base_url.rstrip("/")
        self.configured_model = model
        self.tool_version = ""
        self._probe_cache: tuple[float, bool] | None = None
        self._models_cache: tuple[float, list[str]] | None = None

    # --- availability (installation capability) --------------------------------

    def available(self) -> bool:
        now = time.monotonic()
        if self._probe_cache and now - self._probe_cache[0] < _AVAILABILITY_TTL_SECONDS:
            return self._probe_cache[1]
        try:
            version = self._get("/api/version", timeout=2.0)
            self.tool_version = f"ollama/{version.get('version', '')}"
            reachable = bool(self.resolve_model())
        except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
            reachable = False
        self._probe_cache = (now, reachable)
        return reachable

    def resolve_model(self) -> str:
        """Configured model, else the first installed one (sorted)."""
        if self.configured_model:
            return self.configured_model
        now = time.monotonic()
        if (
            self._models_cache
            and now - self._models_cache[0] < _AVAILABILITY_TTL_SECONDS
        ):
            models = self._models_cache[1]
        else:
            payload = self._get("/api/tags", timeout=5.0)
            models = sorted(
                name
                for name in (m.get("name", "") for m in payload.get("models", []))
                # Embedding models cannot chat; never auto-select one.
                if name and "embed" not in name.lower()
            )
            self._models_cache = (now, models)
        return models[0] if models else ""

    # --- execution -------------------------------------------------------------

    def execute(self, step: PlanStep, ctx: ExecutionContext) -> list[ProducedFile]:
        if step.operation in ("text.translate", "chapters.derive"):
            model = step.params.get("model") or self.resolve_model()
            if not model:
                raise StepExecutionError("provider_error", "No Ollama model installed.")
            if step.operation == "chapters.derive":
                ctx.on_progress(5.0, f"chaptering with {model}")
                return execute_derive(
                    step, ctx, lambda prompt: self._generate(model, prompt, ctx), model
                )
            ctx.on_progress(5.0, f"translating with {model}")
            return execute_translation(
                step, ctx, lambda prompt: self._generate(model, prompt, ctx), model
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

        model = step.params.get("model") or self.resolve_model()
        if not model:
            raise StepExecutionError("provider_error", "No Ollama model installed.")
        prompt = build_summary_prompt(
            text=text,
            language=step.params.get("language", "auto"),
            length=step.params.get("length", "medium"),
            style=step.params.get("style", "structured"),
            output_format=step.params.get("format", "markdown"),
        )
        ctx.on_progress(5.0, f"summarizing with {model}")
        summary = self._generate(model, prompt, ctx)
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
        media_type = "text/markdown" if suffix == ".md" else "text/plain"
        source_language = material.attributes.get("language", "")
        return [
            ProducedFile(
                path=path,
                media_type=media_type,
                attributes={
                    "model": model,
                    "source_language": source_language,
                    "derived_from": "transcript",
                },
            )
        ]

    # --- HTTP ------------------------------------------------------------------

    def _get(self, route: str, timeout: float) -> dict:
        with urllib.request.urlopen(self.base_url + route, timeout=timeout) as response:
            return json.loads(response.read())

    def _generate(self, model: str, prompt: str, ctx: ExecutionContext) -> str:
        # Blocking single call, bounded by the step timeout. Cooperative
        # cancellation cannot interrupt it mid-call (documented limitation of
        # service runners); the job cancels right after the call returns.
        body = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0.2},
            }
        ).encode()
        request = urllib.request.Request(
            self.base_url + "/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=ctx.timeout_seconds
            ) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise StepExecutionError(
                "provider_error", f"Ollama returned HTTP {exc.code}: {detail}"
            )
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise StepExecutionError("provider_error", f"Ollama unreachable: {exc}")
        _warn_if_truncated(prompt, payload, model, ctx)
        content = (payload.get("message") or {}).get("content", "")
        return strip_thinking(content)
