"""Resource analysis orchestration: provider dispatch + TTL cache.

Analysis is distinct from generation (it has its own endpoint) but the two
paths converge: job submission reuses exactly this service, so a fresh cached
analysis makes submission instant (HomeTube's url_info cache, generalized).
"""

from datetime import datetime, timedelta, timezone

from pydantic import TypeAdapter

from content.analysis.cache import AnalysisJsonCache
from content.config import ContentSettings
from content.domain import errors as codes
from content.domain.analysis import (
    AnalysisError,
    ResourceAnalysis,
    SourceAnalysis,
)
from content.domain.errors import (
    RequestRejected,
    ValidationIssue,
    ValidationResult,
)
from content.domain.request import SourceDescriptor
from content.persistence.store import Store, new_id, utcnow
from content.planning.auth import resolve_source_credential
from content.providers.base import AnalysisContext, ProviderRegistry

# Bumped when the shape of the facts an analysis produces changes; stored on
# each addressable record so a stale analyzer can be detected (ADR 0014).
ANALYZER_VERSION = "1"

_SOURCES_ADAPTER = TypeAdapter(list[SourceDescriptor])


def _at_path(exc: AnalysisError, index: int) -> AnalysisError:
    """Re-stamp a provider's issue with the source's position in the request,
    preserving whether the refusal was terminal."""
    return AnalysisError(
        exc.issue.model_copy(update={"path": f"sources[{index}]"}),
        terminal=exc.terminal,
    )


class AnalysisNotFound(Exception):
    """No addressable analysis record exists for this id (→ 404)."""

    def __init__(self, analysis_id: str):
        self.analysis_id = analysis_id
        super().__init__(f"analysis {analysis_id} not found")


class AnalysisExpired(Exception):
    """The record exists but has expired, or its referenced facts are gone
    (→ 410). Never re-derived implicitly — the client must re-analyze."""

    def __init__(self, analysis_id: str):
        self.analysis_id = analysis_id
        super().__init__(f"analysis {analysis_id} has expired")


class AnalysisService:
    def __init__(
        self, store: Store, providers: ProviderRegistry, settings: ContentSettings
    ):
        self._store = store
        self._providers = providers
        self._settings = settings
        self._json_cache = (
            AnalysisJsonCache(
                settings.cache_dir or settings.data_dir / "cache",
                settings.analysis_ttl_hours,
            )
            if settings.cache_enabled
            else None
        )

    def analyze_sources(self, sources: list[SourceDescriptor]) -> ResourceAnalysis:
        """Analyze every source, reusing fresh cached results per resource.

        Raises RequestRejected (feasibility phase) when a source cannot be
        analyzed — unsupported type or provider failure.
        """
        results: list[SourceAnalysis] = []
        issues: list[ValidationIssue] = []
        credential_ids = set(self._settings.credentials)
        for index, source in enumerate(sources):
            # Honour or reject `auth` here too, so POST /analyses is as honest
            # as job submission (INV-100).
            _cred, auth_issue = resolve_source_credential(
                source, credential_ids, f"sources[{index}]"
            )
            if auth_issue is not None:
                issues.append(auth_issue)
                continue
            candidates = self._providers.candidates_for_source(source)
            if not candidates:
                issues.append(
                    ValidationIssue(
                        code=codes.SOURCE_TYPE_NOT_SUPPORTED,
                        path=f"sources[{index}].type",
                        message=(
                            f"Source type '{source.type}' is valid but not supported "
                            "by the current installation."
                        ),
                    )
                )
                continue
            # Several providers can claim the same source (a URL is offered to
            # yt-dlp before the generic page reader). Try them in precedence
            # order and let the *analysis* decide: the first one that can
            # characterise the resource wins. Only the last failure is reported,
            # because an earlier "this is not mine" is routing, not an error.
            # A *terminal* refusal is the exception: a provider that recognises
            # the format and deliberately declines it ends the chain, so its
            # reason reaches the caller intact (D-27).
            entry, failure = None, None
            for provider in candidates:
                entry, error = self._analyze_with(provider, source, index)
                if entry is not None:
                    break
                failure = error.issue if error is not None else None
                if error is not None and error.terminal:
                    break
            if entry is None:
                issues.append(
                    failure
                    or ValidationIssue(
                        code=codes.ANALYSIS_FAILED,
                        path=f"sources[{index}]",
                        message="No provider could analyze this source.",
                    )
                )
                continue
            results.append(entry)

        if issues:
            raise RequestRejected(ValidationResult.failure(issues, phase="feasibility"))
        analysis_id = new_id("ana")
        created_at = utcnow()
        expires_at = self._record_expiry(created_at)
        # Persist the addressable record: it *references* the resource_key facts
        # cache (resource_keys), it does not copy the heavy facts (ADR 0014).
        self._store.save_analysis_record(
            analysis_id,
            [source.model_dump(mode="json") for source in sources],
            [entry.resource_key for entry in results],
            ANALYZER_VERSION,
            created_at,
            expires_at,
        )
        return ResourceAnalysis(
            analysis_id=analysis_id,
            created_at=created_at,
            expires_at=expires_at,
            sources=results,
        )

    def _record_expiry(self, created_at: str) -> str:
        ttl = self._settings.analysis_ttl_hours
        return (datetime.fromisoformat(created_at) + timedelta(hours=ttl)).isoformat()

    def _require_fresh_record(self, analysis_id: str) -> dict:
        """Deterministic gate shared by every analysis_id consumer: absent →
        AnalysisNotFound, present-but-past-expiry → AnalysisExpired. Never
        re-analyzes."""
        record = self._store.load_analysis_record(analysis_id)
        if record is None:
            raise AnalysisNotFound(analysis_id)
        if datetime.fromisoformat(record["expires_at"]) < datetime.now(timezone.utc):
            raise AnalysisExpired(analysis_id)
        return record

    def sources_for_analysis(self, analysis_id: str) -> list[SourceDescriptor]:
        """Stored sources for an addressable analysis — feeds the normal
        pipeline in analysis_id mode on /capabilities and /jobs (which then
        re-derive facts as usual). 404/410 via the shared gate."""
        record = self._require_fresh_record(analysis_id)
        return _SOURCES_ADAPTER.validate_python(record["sources"])

    def get_analysis(self, analysis_id: str) -> ResourceAnalysis:
        """Reconstruct the full analysis for GET /analyses/{id}: join the facts
        from the resource_key cache. A safe read — it NEVER re-runs analysis.
        Missing/stale referenced facts are reported as expiry, not re-derived."""
        record = self._require_fresh_record(analysis_id)
        results: list[SourceAnalysis] = []
        for source, key in zip(record["sources"], record["resource_keys"]):
            cached = self._store.load_fresh_analysis(
                key, self._settings.analysis_ttl_hours
            )
            if cached is None and self._json_cache is not None:
                cached = self._json_cache.load(key)
            if cached is None:
                raise AnalysisExpired(analysis_id)
            results.append(
                SourceAnalysis.model_validate(
                    {**cached, "source_id": source["id"], "resource_key": key}
                )
            )
        return ResourceAnalysis(
            analysis_id=analysis_id,
            created_at=record["created_at"],
            expires_at=record["expires_at"],
            sources=results,
        )

    def _analyze_with(self, provider, source, index: int):
        """Analyse *source* with one provider. Returns ``(entry, None)`` on
        success or ``(None, error)`` — the caller decides whether a failure is a
        routing miss (try the next candidate) or the final answer, using the
        error's ``terminal`` flag. The path is stamped here so callers never
        have to know the source index."""
        # The analysis *cache* is the DB (load_fresh_analysis); the filesystem
        # here is only disposable probe scratch, so it lives under tmp/ — never
        # under cache/ (INV-STORAGE-009).
        analysis_tmp = (
            self._settings.tmp_dir or self._settings.data_dir / "tmp"
        ) / "analysis"
        probe_ctx = AnalysisContext(self._settings, analysis_tmp / "probe")
        try:
            key = provider.resource_key(source, probe_ctx)
        except AnalysisError as exc:
            return None, _at_path(exc, index)

        cached = self._store.load_fresh_analysis(key, self._settings.analysis_ttl_hours)
        # Durable file cache is the source of truth: it survives a DB reset.
        if cached is None and self._json_cache is not None:
            cached = self._json_cache.load(key)
            if cached is not None:
                self._store.save_analysis(new_id("ana"), key, cached)
        if cached is not None:
            if self._json_cache is not None and not self._json_cache.exists(key):
                self._json_cache.save(key, cached)
            return (
                SourceAnalysis.model_validate(
                    {**cached, "source_id": source.id, "resource_key": key}
                ),
                None,
            )

        workdir = analysis_tmp / key.replace(":", "_")
        try:
            entry = provider.analyze(source, AnalysisContext(self._settings, workdir))
        except AnalysisError as exc:
            return None, _at_path(exc, index)
        entry.resource_key = key
        # Persist the resource facts only; installation-dependent capabilities
        # are recomputed at read time (they change when daemons start/stop,
        # independently of the resource).
        payload = entry.model_dump(mode="json")
        self._store.save_analysis(new_id("ana"), key, payload)
        if self._json_cache is not None:
            self._json_cache.save(key, payload)
        return entry, None
