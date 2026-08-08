"""Pure SRT/VTT parsing into canonical transcript segments (contract D8).

The canonical transcript is the structured JSON model; SRT/VTT/text are
derivations of it, never the other way around. These functions are pure and
tolerant: real-world files (especially auto-generated captions) contain
formatting tags, rolling duplicate lines and cue settings.
"""

import re

_TIMESTAMP = re.compile(r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[.,](\d{3})")
_CUE_LINE = re.compile(r"(?P<start>[\d:.,]+)\s+-->\s+(?P<end>[\d:.,]+)")
_TAGS = re.compile(r"<[^>]+>")  # <c>, <i>, <00:00:01.000> inline timestamps


def _parse_timestamp(raw: str) -> float | None:
    match = _TIMESTAMP.search(raw)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes, seconds, millis = (
        int(match.group(2)),
        int(match.group(3)),
        int(match.group(4)),
    )
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def _clean_text(lines: list[str]) -> str:
    text = " ".join(_TAGS.sub("", line).strip() for line in lines)
    return re.sub(r"\s+", " ", text).strip()


def parse_subtitles(content: str) -> list[dict]:
    """Parse SRT or VTT content into ``[{start, end, text}]`` segments.

    Handles both formats with one pass: blocks separated by blank lines, a cue
    timing line per block, optional numeric counters (SRT) and header/NOTE
    blocks (VTT). Consecutive duplicate texts (rolling auto-captions) are
    merged, extending the previous segment's end.
    """
    segments: list[dict] = []
    for block in re.split(r"\n\s*\n", content.replace("\r\n", "\n").strip()):
        lines = [line for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        cue_index = next(
            (i for i, line in enumerate(lines) if _CUE_LINE.search(line)), None
        )
        if cue_index is None:
            continue  # WEBVTT header, NOTE, STYLE blocks, bare counters
        match = _CUE_LINE.search(lines[cue_index])
        start = _parse_timestamp(match.group("start"))
        end = _parse_timestamp(match.group("end"))
        if start is None or end is None:
            continue
        text = _clean_text(lines[cue_index + 1 :])
        if not text:
            continue
        if segments and segments[-1]["text"] == text:
            segments[-1]["end"] = max(segments[-1]["end"], end)
            continue
        segments.append({"start": round(start, 3), "end": round(end, 3), "text": text})
    return segments


def segments_to_text(segments: list[dict]) -> str:
    """Plain-text derivation of the canonical segments."""
    return "\n".join(segment["text"] for segment in segments)
