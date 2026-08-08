"""Content's renderer-neutral document representation.

`model` is the shape, `markdown` is the single parser that produces it, and
`fonts` answers whether a given font can actually draw it. Nothing here imports
a renderer — that direction of dependency is what keeps one parse shared by all
of them.
"""
