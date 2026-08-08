"""Pure prompt construction for the ``text.summarize`` operation.

Server-known templates only (contract D10 — clients never inject executable
templates); every LLM runner (Ollama today, others later) shares this module
so summaries stay consistent across providers. Pure and unit-testable.
"""

import json

from content.providers.base import Material

_LENGTH_GUIDANCE = {
    "short": "Write a concise summary of 3 to 5 sentences.",
    "medium": "Write a summary of one to three paragraphs.",
    "long": "Write a detailed summary covering every significant part.",
}

_STYLE_GUIDANCE = {
    "structured": (
        "Structure the summary with short headings for the main themes, "
        "followed by the key points."
    ),
    "plain": "Write flowing prose without headings or lists.",
    "bullet_points": "Write the summary as a hierarchy of bullet points.",
}


def transcript_text_from_material(material: Material) -> str:
    """Extract the text to summarize from a transcript material (canonical
    JSON preferred, plain text accepted)."""
    content = material.path.read_text(errors="replace")
    if material.path.suffix.lower() == ".json":
        try:
            payload = json.loads(content)
            return "\n".join(
                segment.get("text", "") for segment in payload.get("segments", [])
            ).strip()
        except (json.JSONDecodeError, AttributeError):
            return content.strip()
    return content.strip()


def build_summary_prompt(
    *,
    text: str,
    language: str,
    length: str,
    style: str,
    output_format: str,
) -> str:
    if language and language != "auto":
        language_rule = f"Write the summary in the language '{language}'."
    else:
        language_rule = "Write the summary in the same language as the transcript."
    format_rule = (
        "Format the output as Markdown."
        if output_format == "markdown"
        else "Output plain text without any markup."
    )
    return "\n".join(
        [
            "You summarize transcripts faithfully: never invent facts, keep the",
            "original meaning, and do not add commentary about the task itself.",
            "",
            _LENGTH_GUIDANCE[length],
            _STYLE_GUIDANCE[style],
            language_rule,
            format_rule,
            "",
            "Transcript:",
            '"""',
            text,
            '"""',
        ]
    )


def strip_thinking(response: str) -> str:
    """Remove <think>...</think> blocks emitted by reasoning models."""
    import re

    return re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()


def strip_markdown_fence(response: str) -> str:
    """Unwrap a whole answer that the model enclosed in a Markdown code fence.

    Asked for Markdown, models very often answer with the document wrapped in
    ```` ```markdown … ``` ````. Left in place that fence is not cosmetic: the
    `.md` artifact carries syntax the caller did not ask for, and anything that
    reads the file downstream — a PDF renderer above all — correctly concludes
    the entire document is a code block and lays it out as monospace.

    Only an outer fence that is *empty or explicitly markdown* is removed. A
    fence tagged with a real language is content the model meant to include, so
    a summary that genuinely opens with a Python snippet keeps it.
    """
    import re

    text = response.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) < 2 or lines[-1].strip() != "```":
        return text  # unterminated: leave it alone rather than truncate
    if not re.fullmatch(r"```\s*(markdown|md)?\s*", lines[0]):
        return text
    return "\n".join(lines[1:-1]).strip()
