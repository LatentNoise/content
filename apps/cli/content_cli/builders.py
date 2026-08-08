"""Ergonomic shortcuts normalized to the canonical public contract.

These are pure functions (no I/O) so they are trivially testable and can be
validated against the back-end's Pydantic schema. The CLI never invents a
parallel contract — every shortcut produces a standard GenerationRequest.
"""

SB_PRESETS: dict[str, dict | None] = {
    "disabled": None,
    "default": {
        "remove": ["sponsor", "interaction", "selfpromo"],
        "mark": ["intro", "preview", "outro"],
    },
    "aggressive": {
        "remove": ["sponsor", "selfpromo", "interaction", "intro", "outro", "preview"],
        "mark": [],
    },
    "minimal": {"remove": ["sponsor"], "mark": []},
}


def _split(value: str | None) -> list[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def url_source(url: str, credential: str | None = None) -> dict:
    src: dict = {"id": "main", "type": "url", "uri": url.strip()}
    if credential:
        src["auth"] = {"credential_id": credential}
    return src


def _delivery(folder: str | None, name: str | None) -> dict:
    delivery: dict = {}
    if folder:
        delivery["folder"] = folder
    if name:
        delivery["filename"] = name
    return delivery


def video_request(
    url: str,
    *,
    height: int | None = 1080,
    codec: str = "auto",
    container: str = "source",
    subtitles: str | None = None,
    audio_languages: str | None = None,
    sponsorblock: str = "disabled",
    playlist: bool = False,
    credential: str | None = None,
    folder: str | None = None,
    name: str | None = None,
    reuse: bool = True,
) -> dict:
    selection: dict = {}
    if height:
        selection["max_height"] = height
    if codec != "auto":
        selection["video_codec"] = {"mode": "prefer", "value": codec}
    if audio_languages:
        selection["audio_languages"] = _split(audio_languages)
    options: dict = {"selection": selection, "container": container}
    subs = _split(subtitles)
    if subs:
        options["processing"] = {"embed_subtitles": subs}
    sb = SB_PRESETS.get(sponsorblock)
    if sb:
        options["sponsorblock"] = sb
    output: dict = {"id": "video_main", "type": "video", "options": options}
    if playlist:
        output["scope"] = "each_item"
    delivery = _delivery(folder, name)
    if delivery:
        output["delivery"] = delivery
    return {
        "schema_version": "1.0",
        "sources": [url_source(url, credential)],
        "outputs": [output],
        "execution": {"reuse_existing": reuse},
    }


def audio_request(
    url: str,
    *,
    fmt: str = "source",
    languages: str | None = None,
    sponsorblock: str = "disabled",
    playlist: bool = False,
    credential: str | None = None,
    folder: str | None = None,
    name: str | None = None,
    reuse: bool = True,
) -> dict:
    options: dict = {}
    if fmt != "source":
        options["format"] = fmt
    langs = _split(languages)
    if langs:
        options["languages"] = langs
    sb = SB_PRESETS.get(sponsorblock)
    if sb:
        options["sponsorblock"] = sb
    output: dict = {"id": "audio_main", "type": "audio"}
    if options:
        output["options"] = options
    if playlist:
        output["scope"] = "each_item"
    delivery = _delivery(folder, name)
    if delivery:
        output["delivery"] = delivery
    return {
        "schema_version": "1.0",
        "sources": [url_source(url, credential)],
        "outputs": [output],
        "execution": {"reuse_existing": reuse},
    }
