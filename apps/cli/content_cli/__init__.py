"""content — command-line client for the Content engine."""

from importlib.metadata import PackageNotFoundError, version

# The installed wheel's own version — never a literal. The 0.2.0 release
# shipped `content --version` answering "0.1.0" because this file carried a
# hardcoded string that no release tooling rewrote (it was the one version
# declaration outside `make version`'s lockstep). Metadata cannot drift.
try:
    __version__ = version("content-cli")
except PackageNotFoundError:  # an uninstalled source tree (no dist metadata)
    __version__ = "0+source"
