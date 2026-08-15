"""Normalized validation issues and error codes.

Codes are stable, machine-readable identifiers (see docs/contract.md §6);
human messages may evolve freely.
"""

from pydantic import BaseModel, Field

# Structural codes
UNSUPPORTED_SCHEMA_VERSION = "unsupported_schema_version"
DUPLICATE_ID = "duplicate_id"
UNKNOWN_SOURCE_REFERENCE = "unknown_source_reference"
UNKNOWN_OUTPUT_REFERENCE = "unknown_output_reference"
DEPENDENCY_CYCLE = "dependency_cycle"
AMBIGUOUS_INPUTS = "ambiguous_inputs"
TOO_MANY_INPUTS = "too_many_inputs"
INVALID_OPTION = "invalid_option"
# The body does not match the schema at all (wrong type, unknown field, missing
# required key). Raised by Pydantic and translated into this contract's error
# shape, so a client parses one 422 format rather than two (D-09).
SCHEMA_VIOLATION = "schema_violation"
# Exactly one of `sources` / `analysis_id` must be supplied (addressable
# analyses, ADR 0014).
SOURCES_OR_ANALYSIS_ID_REQUIRED = "sources_or_analysis_id_required"
SOURCES_AND_ANALYSIS_ID_CONFLICT = "sources_and_analysis_id_conflict"

# Analysis-resource codes (addressable analyses, ADR 0014)
ANALYSIS_NOT_FOUND = "analysis_not_found"
ANALYSIS_EXPIRED = "analysis_expired"
# Uploads (ADR 0020). Told apart on purpose: "expired" means upload the file
# again, "not found" means the reference itself is wrong.
UPLOAD_NOT_FOUND = "upload_not_found"
UPLOAD_EXPIRED = "upload_expired"

# Feasibility codes
SOURCE_TYPE_NOT_SUPPORTED = "source_type_not_supported"
OUTPUT_TYPE_NOT_SUPPORTED = "output_type_not_supported"
SCOPE_NOT_SUPPORTED = "scope_not_supported"
PATH_NOT_ALLOWED = "path_not_allowed"
URL_NOT_ALLOWED = "url_not_allowed"
OPTION_NOT_SUPPORTED = "option_not_supported"
CAPABILITY_UNAVAILABLE = "capability_unavailable"
CONSTRAINT_UNSATISFIABLE = "constraint_unsatisfiable"
CREDENTIAL_NOT_AVAILABLE = "credential_not_available"
AUTH_METHOD_NOT_SUPPORTED = "auth_method_not_supported"
ANALYSIS_FAILED = "analysis_failed"
ANALYSIS_STALE = "analysis_stale"
IDEMPOTENCY_CONFLICT = "idempotency_conflict"

# Warning codes
PREFERRED_PROVIDER_UNAVAILABLE = "preferred_provider_unavailable"
PREFERENCE_UNAVAILABLE = "preference_unavailable"
CAPABILITY_UNKNOWN = "capability_unknown"
TRANSCODE_REQUIRED = "transcode_required"
PARTIAL_OUTPUT = "partial_output"
CONSTRAINT_CHECK_DEFERRED = "constraint_check_deferred"
REUSE_UNAVAILABLE = "reuse_unavailable"


class ValidationIssue(BaseModel):
    code: str
    path: str = ""
    message: str
    details: dict = Field(default_factory=dict)


class ValidationResult(BaseModel):
    valid: bool
    phase: str = "structural"  # structural | feasibility
    errors: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)

    @classmethod
    def failure(
        cls,
        errors: list[ValidationIssue],
        phase: str = "structural",
        warnings: list[ValidationIssue] | None = None,
    ) -> "ValidationResult":
        return cls(valid=False, phase=phase, errors=errors, warnings=warnings or [])

    @classmethod
    def success(
        cls, phase: str = "structural", warnings: list[ValidationIssue] | None = None
    ) -> "ValidationResult":
        return cls(valid=True, phase=phase, warnings=warnings or [])


class RequestRejected(Exception):
    """A request failed structural or feasibility validation."""

    def __init__(self, result: ValidationResult):
        self.result = result
        codes = ", ".join(issue.code for issue in result.errors)
        super().__init__(f"request rejected ({result.phase}): {codes}")
