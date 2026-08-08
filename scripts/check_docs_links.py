"""Resolve every relative Markdown link in the repo's documentation."""
import re, sys
from pathlib import Path

ROOT = Path(".").resolve()
LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+?)(?:\s+\"[^\"]*\")?\)")
targets = list(ROOT.glob("*.md")) + sorted(ROOT.glob("docs/**/*.md")) + sorted(ROOT.glob("work/*.md"))

broken = []
checked = 0
for doc in targets:
    for label, href in LINK.findall(doc.read_text(encoding="utf-8")):
        if href.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path, _, _anchor = href.partition("#")
        if not path:
            continue
        checked += 1
        resolved = (doc.parent / path).resolve()
        if not resolved.exists():
            broken.append((str(doc.relative_to(ROOT)), href, label[:40]))

print(f"documents scanned : {len(targets)}")
print(f"relative links    : {checked}")
print(f"broken            : {len(broken)}")
for doc, href, label in broken:
    print(f"   {doc}: [{label}]({href})")
sys.exit(1 if broken else 0)
