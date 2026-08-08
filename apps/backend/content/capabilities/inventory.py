"""A read-only description of the engine's architecture (ADR 0013) for the
operations console: the public capability catalog, the internal transformation
operations, and which implementations (runners) are installed and available.

It ties the three layers together in one payload — capability → variants →
operations, and operation → implementations → availability — so the console can
show *why* a capability can or cannot run in this installation.
"""

from content.capabilities.catalog import all_capabilities
from content.planning.transformations import build_registry
from content.providers.base import ProviderRegistry


def describe_architecture(providers: ProviderRegistry) -> dict:
    registry = build_registry(providers)
    availability = {r["name"]: r for r in providers.describe()}

    operations = []
    for op in registry.operations():
        definition = registry.definition(op)
        impls = registry.implementations_for(op)
        operations.append(
            {
                "operation": op,
                "input_kinds": list(definition.input_kinds),
                "output_kinds": list(definition.output_kinds),
                "deterministic": definition.deterministic,
                "implementations": [
                    {
                        "runner": impl.runner,
                        "version": impl.version,
                        "available": availability.get(impl.runner, {}).get(
                            "available", False
                        ),
                    }
                    for impl in sorted(impls, key=lambda i: i.runner)
                ],
            }
        )

    capabilities = [
        {
            "id": cap.id,
            "title": cap.title,
            "description": cap.description,
            "output_type": cap.output_type,
            "variants": [
                {
                    "id": variant.id,
                    "operations": list(variant.operations),
                    "requires_materials": list(variant.requires_materials),
                    "option_groups": list(variant.option_groups),
                }
                for variant in cap.variants
            ],
        }
        for cap in all_capabilities()
    ]

    return {"capabilities": capabilities, "operations": operations}
