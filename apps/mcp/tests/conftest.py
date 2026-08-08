import pathlib
import sys

# Make the MCP package importable when pytest runs from the repo root.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
