"""Structural validation of the public contract (phase 1)."""

import pytest
from pydantic import ValidationError

from content.domain.validation import validate_structure
from tests.conftest import make_request, minimal_payload


def codes_of(result):
    return [issue.code for issue in result.errors]


def test_minimal_request_is_valid():
    result = validate_structure(make_request(minimal_payload()))
    assert result.valid
    assert result.errors == []


def test_reserved_output_types_are_schema_valid():
    request = make_request(minimal_payload(outputs=[{"id": "t", "type": "transcript"}]))
    assert validate_structure(request).valid  # rejected later, at feasibility


def test_unknown_output_type_is_rejected_by_schema():
    with pytest.raises(ValidationError):
        make_request(minimal_payload(outputs=[{"id": "x", "type": "whisper"}]))


def test_unknown_extra_field_is_rejected():
    with pytest.raises(ValidationError):
        make_request({**minimal_payload(), "operations": []})


def test_unsupported_schema_major_version():
    result = validate_structure(make_request(minimal_payload(schema_version="2.0")))
    assert codes_of(result) == ["unsupported_schema_version"]


def test_duplicate_ids_detected():
    payload = minimal_payload(
        sources=[
            {"id": "main", "type": "url", "uri": "https://a"},
            {"id": "main", "type": "url", "uri": "https://b"},
        ],
        outputs=[
            {"id": "audio", "type": "audio", "from_sources": ["main"]},
            {"id": "audio", "type": "metadata", "from_sources": ["main"]},
        ],
    )
    result = validate_structure(make_request(payload))
    assert codes_of(result).count("duplicate_id") == 2


def test_unknown_source_reference():
    payload = minimal_payload(
        outputs=[{"id": "audio", "type": "audio", "from_sources": ["missing"]}]
    )
    result = validate_structure(make_request(payload))
    assert "unknown_source_reference" in codes_of(result)
    assert result.errors[0].path == "outputs[0].from_sources[0]"


def test_dependency_cycle_detected():
    payload = minimal_payload(
        outputs=[
            {"id": "a", "type": "summary", "from_outputs": ["b"]},
            {"id": "b", "type": "summary", "from_outputs": ["a"]},
        ]
    )
    result = validate_structure(make_request(payload))
    assert "dependency_cycle" in codes_of(result)


def test_ambiguous_inputs_with_multiple_sources_and_no_primary():
    payload = minimal_payload(
        sources=[
            {"id": "one", "type": "url", "uri": "https://a"},
            {"id": "two", "type": "url", "uri": "https://b"},
        ],
    )
    result = validate_structure(make_request(payload))
    assert "ambiguous_inputs" in codes_of(result)


def test_unique_primary_source_resolves_implicit_inputs():
    payload = minimal_payload(
        sources=[
            {"id": "one", "type": "url", "role": "primary", "uri": "https://a"},
            {"id": "two", "type": "url", "role": "context", "uri": "https://b"},
        ],
    )
    assert validate_structure(make_request(payload)).valid


def test_media_output_rejects_from_outputs():
    payload = minimal_payload(
        outputs=[
            {"id": "m", "type": "metadata", "from_sources": ["main"]},
            {"id": "audio", "type": "audio", "from_outputs": ["m"]},
        ]
    )
    result = validate_structure(make_request(payload))
    assert "too_many_inputs" in codes_of(result)


def test_auth_requires_exactly_one_reference():
    with pytest.raises(ValidationError):
        make_request(
            minimal_payload(
                sources=[
                    {
                        "id": "main",
                        "type": "url",
                        "uri": "https://a",
                        "auth": {"credential_id": "c", "session_id": "s"},
                    }
                ]
            )
        )


def test_canonical_dump_materializes_defaults():
    dump = make_request(minimal_payload()).canonical_dump()
    assert dump["execution"]["failure_policy"] == "required_only"
    assert dump["outputs"][0]["scope"] == "single"
    assert dump["outputs"][0]["required"] is True


# --- the reserved surface: accepted, never silently ignored (D-01) --------------


def _reserved(payload_extra: dict):
    return validate_structure(make_request(minimal_payload(**payload_extra)))


@pytest.mark.parametrize(
    "payload_extra, path",
    [
        ({"execution": {"mode": "sync"}}, "execution.mode"),
        ({"execution": {"priority": "high"}}, "execution.priority"),
        ({"execution": {"retention": {"outputs": "1d"}}}, "execution.retention"),
        (
            {"preferences": {"execution_location": "local"}},
            "preferences.execution_location",
        ),
        (
            {"constraints": {"network": {"allow_remote_processing": False}}},
            "constraints.network.allow_remote_processing",
        ),
        ({"preferences": {"language": "fr"}}, "preferences.language"),
    ],
)
def test_a_reserved_field_is_refused_not_ignored(payload_extra, path):
    """Every one of these used to be accepted and do nothing.

    Silence taught clients to keep sending them; the refusal names the field and
    a remedy, and `option_not_supported` is the same stable code the rest of the
    contract uses for "valid, but this engine will not do it".
    """
    result = _reserved(payload_extra)
    assert not result.valid, f"{path} was accepted with no effect"
    issue = next(i for i in result.errors if i.path == path)
    assert issue.code == "option_not_supported"
    assert issue.message.strip(), "a refusal must say why"


def test_hints_are_refused_because_facts_decide_routing():
    payload = minimal_payload()
    payload["sources"][0]["hints"] = {"preferred_provider": "ytdlp"}
    result = validate_structure(make_request(payload))
    assert not result.valid
    assert any(i.path == "sources[].hints" for i in result.errors)


def test_the_default_request_touches_no_reserved_field():
    """The refusals must not fire on a request that asked for nothing unusual —
    otherwise every existing client breaks."""
    assert validate_structure(make_request(minimal_payload())).valid


def test_a_canonical_request_survives_its_own_round_trip():
    """Retry replays the stored canonical body verbatim.

    A reserved check written as "the client mentioned this field" would refuse
    the engine's own round trip, because the canonical dump names every field.
    """
    request = make_request(minimal_payload())
    replayed = make_request(request.canonical_dump())
    assert validate_structure(replayed).valid


def test_advisory_reserved_fields_warn_rather_than_refuse():
    """`optimize_for` changes nothing, but the artifact is still the one asked
    for — refusing the whole job over a hint would be obstructive."""
    from content.domain.reserved import check_reserved

    request = make_request(minimal_payload(preferences={"optimize_for": "speed"}))
    refusals, warnings = check_reserved(request)
    assert refusals == []
    assert [w.path for w in warnings] == ["preferences.optimize_for"]
    assert validate_structure(request).valid


# --- the guard against the NEXT silent ignore -----------------------------------


def _leaf_paths(model, prefix: str = "") -> set[str]:
    """Every leaf field of a Pydantic model, as dotted public paths."""
    from pydantic import BaseModel

    leaves: set[str] = set()
    for name, info in model.model_fields.items():
        path = f"{prefix}.{name}" if prefix else name
        annotation = info.annotation
        nested = [
            arg
            for arg in (getattr(annotation, "__args__", None) or [annotation])
            if isinstance(arg, type) and issubclass(arg, BaseModel)
        ]
        if nested:
            for sub in nested:
                leaves |= _leaf_paths(sub, path)
        else:
            leaves.add(path)
    return leaves


# Read by the engine today. Each entry is a field some planner, provider,
# executor or store actually consults — verified by grepping for its use.
IMPLEMENTED_PATHS = {
    "schema_version",
    "analysis_id",
    "sources",
    "outputs",
    "execution.failure_policy",
    "execution.reuse_existing",
    "execution.idempotency_key",
    "preferences.providers",
    "constraints.privacy.allow_cloud_providers",
    "constraints.resources.max_runtime_seconds",
    "constraints.resources.max_output_bytes",
    "constraints.content.allowed_languages",
}


def test_no_public_field_is_accepted_without_being_classified():
    """The guard-rail that makes D-01 unrepeatable.

    Every request-level field must be one of: read by the engine, or declared in
    `content/domain/reserved.py` with what happens when a client sets it. A new
    field that is merely accepted fails here, at the moment it is added, rather
    than after someone has shipped code against it.

    Scope is the request-level blocks. Per-output `options` are deliberately out:
    they are validated per capability and refused with `option_not_supported`
    by the planner, which is a different mechanism with its own tests.
    """
    from content.domain.request import GenerationRequest
    from content.domain.reserved import RESERVED_PATHS

    request_level = {
        path
        for path in _leaf_paths(GenerationRequest)
        if path.split(".")[0] in ("execution", "preferences", "constraints")
    }
    classified = IMPLEMENTED_PATHS | RESERVED_PATHS
    unclassified = request_level - classified
    assert not unclassified, (
        "these public fields are accepted but neither read nor declared "
        f"reserved: {sorted(unclassified)} — read them in the engine, or add "
        "them to content/domain/reserved.py with a refusal or a warning"
    )
