"""The per-output half of the D-01 guard: an option that changes nothing fails here.

`test_contract_validation.py` sweeps the *request-level* blocks and refuses to let
a field be merely accepted: every leaf of `execution`, `preferences` and
`constraints` must be read by the engine or declared in `domain/reserved.py`. Its
docstring deferred the per-output `options` to "a different mechanism with its own
tests" — the planner's `option_not_supported` refusals. That mechanism is real, but
it is not equivalent: it fires on the options a planner *looks at*, so an option no
planner looks at is exactly what it cannot see. `transcript.timestamps` is how that
was discovered, by hand, in November 2026.

So this file asks the question empirically instead of from a list. For each option
leaf of each output type, it plans the same request twice, changing only that
option, and demands one of two honest answers:

- **the plan differs** — the option reached the plan, so something acts on it; or
- **the request is refused** — "valid but not implemented" (INV-014), which is a
  first-class answer in this engine.

A third outcome — the plan is byte-identical and nothing complained — is the
silent ignore D-01 exists to make unrepeatable, and it fails here unless the path
is declared in `INERT` with its reason and the decision it waits on.

Two properties worth keeping. The comparison is checked for soundness before it is
trusted: planning the base twice must produce the same bytes, so a nondeterministic
plan field fails loudly instead of reading as "differs" forever. And `INERT` is
checked in both directions: the day an exempted option starts changing the plan,
this test says to delete the entry rather than leaving a stale excuse behind.
"""

from __future__ import annotations

import json
import re
import typing

import pytest
from pydantic import BaseModel

from content.analysis.service import AnalysisService
from content.domain import request as rq
from content.domain.errors import RequestRejected
from content.identity import LOCAL_OWNER
from content.planning.planner import build_plan
from tests.conftest import make_request

# Accepted, read by nothing, and waiting on a ruling rather than on work. Each
# entry is a promise to a client that the engine does not keep, so the list is
# meant to stay empty; an entry is a decision Yann has not taken yet, never a
# shortcut for an option that is simply hard to exercise.
INERT: dict[tuple[str, str], str] = {
    ("transcript", "timestamps"): (
        "`none` is published as a working value and read by nothing: the only "
        "consumer of the field is the `== 'word'` refusal in the planner, and no "
        "transcript planner passes it through. Refusing it changes the public "
        "contract; honouring it needs a timing-less canonical shape (D8). See "
        "work/NEXT.md, Waiting on Yann item 12."
    ),
}

# Output types this test engine cannot plan at all: the fake installation in
# conftest offers the yt-dlp operations plus a transcript and a summarizer, so a
# PDF or a keyframe strip is refused before any option is looked at. Their options
# are unaudited here rather than declared sound.
#
# `markdown` and `chapters` were in this list when it was written from memory, and
# `test_an_unplannable_type_really_cannot_be_planned` threw both out: `markdown`
# carries no options at all, and `chapters` plans fine on a source with no
# declared chapters (it derives them). That is the entire point of making the list
# earn its place.
NOT_PLANNABLE = {
    "document_text",
    "keyframes",
    "pdf",
    "synced_audio",
}

# Option shapes this installation cannot plan even at their baseline: asking for
# a cut needs a `video.cut` runner, and the fake registry offers the acquisition
# operations only. Declared rather than skipped silently, so a path cannot drop
# out of the audit without showing up in a diff.
UNPLANNABLE_SHAPES = {
    ("video", "cut.start"),
    ("video", "cut.end"),
    ("video", "cut.mode"),
}

# Options the schema requires before a request of that type is even valid.
REQUIRED_OPTIONS: dict[str, dict] = {
    "subtitles": {"languages": ["en"]},
    "translation": {"target_language": "es"},
}

# Options that must already be set for the field under test to mean anything.
# `cut_mode` with nothing to cut is not ignored, it is inapplicable — and a test
# that did not set this up read it as a silent ignore on the first pass.
CONTEXT: dict[tuple[str, str], dict] = {
    ("video", "sponsorblock.cut_mode"): {"sponsorblock": {"remove": ["sponsor"]}},
    ("audio", "sponsorblock.cut_mode"): {"sponsorblock": {"remove": ["sponsor"]}},
    ("video", "cut.start"): {"cut": {"end": "30"}},
    ("video", "cut.end"): {"cut": {"end": "30"}},
    ("video", "cut.mode"): {"cut": {"end": "30"}},
    ("video", "selection.video_codec.mode"): {
        "selection": {"video_codec": {"value": "h264"}}
    },
    ("video", "selection.video_codec.value"): {
        "selection": {"video_codec": {"value": "h264"}}
    },
    ("video", "selection.audio_codec.mode"): {
        "selection": {"audio_codec": {"value": "aac"}}
    },
    ("video", "selection.audio_codec.value"): {
        "selection": {"audio_codec": {"value": "aac"}}
    },
}

# A value different from the default, for the fields whose type does not offer
# one (free strings, numbers, lists). Literal fields need no entry: the next
# value of the Literal is a legitimate alternative by construction.
#
# Two entries exist for a sharper reason. `thumbnail.source` and
# `transcript.source` default to `auto`, which *resolves* to one of the other
# values from the analysis facts — so comparing `auto` against the value it
# resolves to on this source proves nothing either way. Each is therefore
# audited against a value `auto` does not resolve to here, and the refusal that
# comes back is itself the proof the field is read: an ignored field could not
# route to a capability and find it missing.
ALTERNATIVE: dict[tuple[str, str], object] = {
    ("thumbnail", "source"): "generate",
    ("transcript", "source"): "speech_to_text",
    ("audio", "languages"): ["fr"],
    ("audio", "sponsorblock.mark"): ["sponsor"],
    ("audio", "sponsorblock.remove"): ["sponsor"],
    ("subtitles", "languages"): ["fr"],
    ("summary", "language"): "fr",
    ("thumbnail", "at"): "10",
    ("thumbnail", "max_width"): 320,
    ("transcript", "language"): "fr",
    ("translation", "source_language"): "fr",
    ("translation", "target_language"): "de",
    ("video", "cut.end"): "20",
    ("video", "cut.start"): "5",
    ("video", "processing.embed_subtitles"): ["en"],
    ("video", "selection.audio_codec.value"): "opus",
    ("video", "selection.audio_languages"): ["fr"],
    ("video", "selection.max_height"): 720,
    ("video", "selection.video_codec.value"): "vp9",
    ("video", "sponsorblock.mark"): ["sponsor"],
    ("video", "sponsorblock.remove"): ["sponsor"],
}


def _option_leaves(model, prefix: str = "") -> list[tuple[str, object]]:
    """Every leaf field of an options model, as (dotted path, FieldInfo)."""
    leaves: list[tuple[str, object]] = []
    for name, info in model.model_fields.items():
        path = f"{prefix}.{name}" if prefix else name
        nested = [
            arg
            for arg in (getattr(info.annotation, "__args__", None) or [info.annotation])
            if isinstance(arg, type) and issubclass(arg, BaseModel)
        ]
        if nested:
            for sub in nested:
                leaves += _option_leaves(sub, path)
        else:
            leaves.append((path, info))
    return leaves


def _output_option_models() -> dict[str, type[BaseModel]]:
    """Every public output type that carries a typed options model."""
    models: dict[str, type[BaseModel]] = {}
    for name in dir(rq):
        model = getattr(rq, name)
        if not (
            isinstance(model, type)
            and issubclass(model, BaseModel)
            and name.endswith("Output")
        ):
            continue
        options = model.model_fields.get("options")
        if options is None or not (
            isinstance(options.annotation, type)
            and issubclass(options.annotation, BaseModel)
        ):
            continue  # ReservedOutput carries a bare dict, validated elsewhere
        type_values = typing.get_args(model.model_fields["type"].annotation)
        if len(type_values) == 1:
            models[type_values[0]] = options.annotation
    return models


def _literal_alternative(info) -> object | None:
    """Another legal value of a Literal field, or None when there is no other."""
    annotation = info.annotation
    candidates = (
        typing.get_args(annotation)
        if typing.get_origin(annotation) is typing.Literal
        else ()
    )
    if not candidates:
        # `Literal[...] | None` and friends.
        for arg in getattr(annotation, "__args__", None) or ():
            if typing.get_origin(arg) is typing.Literal:
                candidates = typing.get_args(arg)
                break
    for value in candidates:
        if value != info.default:
            return value
    if annotation is bool:
        return not info.default
    return None


def _merge(base: dict, extra: dict) -> dict:
    """Deep-merge option dicts; `extra` wins on a leaf."""
    merged = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _at_path(path: str, value: object) -> dict:
    for part in reversed(path.split(".")):
        value = {part: value}
    return value  # type: ignore[return-value]


def _fingerprint(plan) -> str:
    """A plan's content, with the per-run identifiers taken out.

    `plan_id` and `analysis_id` are freshly minted on every call, so comparing
    raw dumps reports every option as meaningful — which is how the first run of
    this audit mistook the whole contract for sound.
    """
    text = json.dumps(json.loads(plan.model_dump_json()), sort_keys=True, default=str)
    return re.sub(r"(ana|plan|job)_[0-9a-f]{16,}", r"\1_ID", text)


CASES = [
    (output_type, path, info)
    for output_type, options_model in sorted(_output_option_models().items())
    if output_type not in NOT_PLANNABLE
    for path, info in _option_leaves(options_model)
]


@pytest.mark.parametrize(
    "output_type,path,info",
    CASES,
    ids=[f"{output_type}.{path}" for output_type, path, _ in CASES],
)
def test_an_accepted_option_changes_the_plan_or_is_refused(
    output_type, path, info, store, providers, settings
):
    alternative = ALTERNATIVE.get((output_type, path), _literal_alternative(info))
    if alternative is None:
        # One legal value means there is no choice to honour or to ignore.
        pytest.skip(f"{output_type}.{path} has no second legal value")

    service = AnalysisService(store, providers, settings)

    def plan(options: dict):
        request = make_request(
            {
                "schema_version": "1.0",
                "sources": [
                    {"id": "main", "type": "url", "uri": "https://example.com/video"}
                ],
                "outputs": [{"id": "o1", "type": output_type, "options": options}],
            }
        )
        analysis = service.analyze_sources(LOCAL_OWNER, list(request.sources))
        return _fingerprint(build_plan(request, analysis, providers, settings))

    base_options = _merge(
        REQUIRED_OPTIONS.get(output_type, {}), CONTEXT.get((output_type, path), {})
    )
    try:
        baseline = plan(base_options)
    except RequestRejected:
        assert (output_type, path) in UNPLANNABLE_SHAPES, (
            f"{output_type}.{path}'s own baseline is refused by this test "
            "engine, so the option cannot be audited — give it the CONTEXT it "
            "needs, or declare it in UNPLANNABLE_SHAPES with the runner it wants"
        )
        pytest.skip(f"{output_type}.{path}: baseline not plannable here")

    assert (output_type, path) not in UNPLANNABLE_SHAPES, (
        f"{output_type}.{path} is declared unplannable but its baseline plans "
        "now — delete the entry and let the audit cover it"
    )
    assert baseline == plan(base_options), (
        f"planning {output_type}.{path}'s baseline twice gave two different plans, "
        "so this comparison cannot tell a read option from a nondeterministic one "
        "— fix the plan's determinism (or widen _fingerprint) before trusting it"
    )

    try:
        altered = plan(_merge(base_options, _at_path(path, alternative)))
    except RequestRejected:
        return  # "valid but not implemented" is an honest answer.

    reason = INERT.get((output_type, path))
    if altered != baseline:
        assert reason is None, (
            f"{output_type}.{path} is declared inert in INERT, but setting it to "
            f"{alternative!r} does change the plan — delete the entry, it is now "
            "an excuse for something that no longer happens"
        )
        return

    assert reason is not None, (
        f"{output_type}.{path} is accepted by the schema, and setting it to "
        f"{alternative!r} produces a byte-identical plan with no refusal and no "
        "warning: a client reads it as supported and it does nothing. Read it in "
        "the engine, refuse it with option_not_supported, or declare it in this "
        "file's INERT map with the decision it is waiting on."
    )


def test_no_output_type_is_excused_from_the_audit_without_reason():
    """`NOT_PLANNABLE` is self-cleaning, which is the only version of it worth
    having.

    The case list is generated from the output models, so asserting that every
    type is "audited or excused" would be a tautology — `audited` is derived by
    subtracting `NOT_PLANNABLE`, so the two can never disagree. (That tautology
    was written here first, and caught by mutating the list.) What can actually
    go wrong is an entry that stops being true: the day the fake installation
    grows a runner, a type listed here starts planning and would stay unaudited
    forever. So each entry is made to earn its place.
    """
    known = set(_output_option_models())
    assert NOT_PLANNABLE <= known, (
        f"NOT_PLANNABLE names types that no longer exist: "
        f"{sorted(NOT_PLANNABLE - known)}"
    )


@pytest.mark.parametrize("output_type", sorted(NOT_PLANNABLE))
def test_an_unplannable_type_really_cannot_be_planned(
    output_type, store, providers, settings
):
    service = AnalysisService(store, providers, settings)
    request = make_request(
        {
            "schema_version": "1.0",
            "sources": [
                {"id": "main", "type": "url", "uri": "https://example.com/video"}
            ],
            "outputs": [
                {
                    "id": "o1",
                    "type": output_type,
                    "options": REQUIRED_OPTIONS.get(output_type, {}),
                }
            ],
        }
    )
    analysis = service.analyze_sources(LOCAL_OWNER, list(request.sources))
    try:
        build_plan(request, analysis, providers, settings)
    except RequestRejected:
        return
    pytest.fail(
        f"{output_type} plans on this test engine now, so its options can be "
        "audited — remove it from NOT_PLANNABLE"
    )
