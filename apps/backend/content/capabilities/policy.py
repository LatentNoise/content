"""Instance policies and the request overlay (ADR 0013, rule R4).

Policies express what the *installation* allows (e.g. may cloud runners be
used). A request may carry constraints, but they can only **restrict** — the
effective policy is the intersection. A request can never re-enable what the
instance forbids.
"""

from dataclasses import dataclass

from content.config import ContentSettings


@dataclass(frozen=True)
class EffectivePolicy:
    """The resolved policy the resolver enforces against implementations."""

    allow_cloud_providers: bool = True

    def allows_runner(self, runner) -> bool:
        location = getattr(runner, "location", "local")
        if location == "cloud" and not self.allow_cloud_providers:
            return False
        return True


@dataclass(frozen=True)
class RequestConstraints:
    """Optional per-request overlay. ``None`` means 'no opinion' (keep the
    instance value); a concrete value may only tighten it (R4)."""

    allow_cloud_providers: bool | None = None


def instance_policy_from_settings(settings: ContentSettings) -> EffectivePolicy:
    # No instance-level kill switch yet; cloud runners are allowed by default and
    # gated by their own availability (an API key must be present). A future
    # setting can flip this without touching the resolver.
    _ = settings
    return EffectivePolicy(allow_cloud_providers=True)


def effective_policy(
    instance: EffectivePolicy, request: RequestConstraints | None = None
) -> EffectivePolicy:
    """Intersection (R4): a request constraint can only restrict the instance."""
    allow_cloud = instance.allow_cloud_providers
    if request is not None and request.allow_cloud_providers is not None:
        allow_cloud = allow_cloud and request.allow_cloud_providers
    return EffectivePolicy(allow_cloud_providers=allow_cloud)
