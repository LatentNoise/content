"""The capability layer (ADR 0013).

Turns *what a source factually is* (analysis) into *what the product can offer*
(resolved capabilities), by evaluating the explicit Capability Catalog against
the transformation registry, the installed implementations and the instance
policies. This package holds the public vocabulary (``catalog``) and, later, the
resolver — the single outward projection every client renders from.
"""
