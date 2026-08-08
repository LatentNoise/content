"""Codec-name normalization shared by providers.

Providers report codecs in their own dialect (yt-dlp: ``avc1.64001f``,
``av01.0.08M.08``, ``vp09.00.10.08``; ffprobe: ``h264``, ``av1``, ``vp9``).
The contract speaks the normalized names (``h264``, ``av1``, ``vp9``, ``aac``,
``opus``); this module is the single translation point.
"""

_VIDEO_PREFIXES = {
    "avc": "h264",
    "h264": "h264",
    "av01": "av1",
    "av1": "av1",
    "vp09": "vp9",
    "vp9": "vp9",
}

_AUDIO_PREFIXES = {
    "mp4a": "aac",
    "aac": "aac",
    "opus": "opus",
}


def normalize_video_codec(raw: str | None) -> str | None:
    """Normalized codec name, or None when unknown/absent."""
    if not raw:
        return None
    lowered = raw.lower()
    for prefix, name in _VIDEO_PREFIXES.items():
        if lowered.startswith(prefix):
            return name
    return None


def normalize_audio_codec(raw: str | None) -> str | None:
    if not raw:
        return None
    lowered = raw.lower()
    for prefix, name in _AUDIO_PREFIXES.items():
        if lowered.startswith(prefix):
            return name
    return None
