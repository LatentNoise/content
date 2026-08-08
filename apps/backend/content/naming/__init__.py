"""Artifact naming (ADR 0017): the engine names what it produces.

Import from the submodules (``content.naming.sanitize``,
``content.naming.engine``) — this package init stays import-free because
``domain.request`` depends on the sanitizer while the engine depends on the
domain: a package-level re-export would tie the knot.
"""
