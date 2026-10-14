"""Of the open Trivy alerts, how many can anybody actually fix?

`security-scan.yml` points Trivy at the four published images weekly and
uploads the findings as SARIF. That is the right thing to scan — ADR 0026 —
but it produces hundreds of alerts, and the Security tab sorts them by
severity, which is the one axis that does not help here. A CRITICAL with no
published fix is nobody's work; a MEDIUM whose fix shipped in Debian last week
is a rebuild away. The useful split is **fixable or not**, and the tab does not
offer it.

So this script reads the open alerts back out of the API and reports, per
scanned image, how many carry a non-empty `Fixed Version` — which Trivy only
fills once the distro or the index has actually published a fix. For a Debian
package that means *rebuild that image* and the finding is gone. For one baked
into a base image this repository does not build it does not: the backend's
`urllib3` and `msgpack` live inside `jauderho/yt-dlp`, and the daily refresh
has rebuilt on the newest base every day without clearing them. The report
states both halves rather than guessing which a row is.

    GH_TOKEN=$(gh auth token) python3 .github/scripts/security_alert_summary.py

Why it exists at all: `work/NEXT.md` carried the claim *"every one inspected
reports an empty Fixed Version, so there is nothing to decide and nothing to
do"* from 2026-09-28 to 2026-10-14. It was true when written and false by the
time anybody re-read it, and re-establishing that took a night pass most of an
hour of hand-querying. The claim was not the problem; the absence of a cheap
re-check was. A number nobody can refresh in one command becomes a stale fact
that keeps asserting.

Standard library only, so it needs no install step. Exits 0 whether or not
fixable alerts exist — "upstream published a fix" is a finding, not a failure —
and non-zero only when the check itself could not be carried out, because a
summary that reports "nothing fixable" when it never ran is worse than a red
job.
"""

from __future__ import annotations

import collections
import json
import os
import re
import sys
import urllib.error
import urllib.request

REPO = os.getenv("GITHUB_REPOSITORY", "LatentNoise/content")
API = "https://api.github.com/repos/{repo}/code-scanning/alerts"
PER_PAGE = 100
TIMEOUT = 30

# Trivy writes these as plain lines in the alert message. `Fixed Version` is
# absent entirely when no fix is published, which is the case this script
# exists to separate from the other one.
#
# The character classes are `[ \t]`, never `\s`: `\s` matches a newline, so
# `Fixed Version:\s*(\S.*)` against a message whose `Fixed Version` line is
# blank happily skips the line break and returns the *next* line — the `Link:`
# URL, read as though it were a version number. The one test that fails
# without this found it.
PACKAGE_RE = re.compile(r"^Package:[ \t]*(\S+)", re.MULTILINE)
INSTALLED_RE = re.compile(r"^Installed Version:[ \t]*(\S+)", re.MULTILINE)
FIXED_RE = re.compile(r"^Fixed Version:[ \t]*(\S[^\n]*?)[ \t]*$", re.MULTILINE)

SEVERITY_ORDER = ["critical", "high", "medium", "low", "none"]


def fail(message: str) -> None:
    print(f"security_alert_summary: {message}", file=sys.stderr)
    raise SystemExit(1)


def severity_of(alert: dict) -> str:
    """The severity to report, preferring the CVSS-derived one.

    GitHub exposes two: `security_severity_level` for alerts that carry a CVSS
    score, and `severity` (`error`/`warning`/`note`) for the rest. Trivy fills
    the first for anything with a CVE, so it is the one that matches what the
    Security tab shows — but it is `null` often enough that falling back
    matters: the xz-utils rebuild advisories arrive with no CVSS at all.
    """
    rule = alert.get("rule") or {}
    return (
        rule.get("security_severity_level") or rule.get("severity") or "none"
    ).lower()


def image_of(alert: dict) -> str:
    """Which image the alert belongs to.

    `security-scan.yml` uploads one SARIF per image under its own category
    (`trivy-content-studio`, …), and that category is the only field that says
    which image a finding came from. The alert *path* does not: an OS-package
    finding is pathed at the image name, but a Python-package finding in the
    very same image is pathed `Python` or at a `dist-info/METADATA` inside it.
    Grouping by path therefore splits one image across several buckets and
    hides the backend's findings completely, which is how a night pass first
    concluded the backend image had none.
    """
    category = (alert.get("most_recent_instance") or {}).get("category")
    return category or "(uncategorised)"


def fixed_version(alert: dict) -> str:
    """The published fix for this alert, or "" when upstream has published none."""
    text = ((alert.get("most_recent_instance") or {}).get("message") or {}).get(
        "text", ""
    )
    match = FIXED_RE.search(text)
    return match.group(1) if match else ""


def package_of(alert: dict) -> tuple[str, str]:
    """`(name, installed version)` of the affected package, or `("", "")`."""
    text = ((alert.get("most_recent_instance") or {}).get("message") or {}).get(
        "text", ""
    )
    name = PACKAGE_RE.search(text)
    installed = INSTALLED_RE.search(text)
    return (name.group(1) if name else "", installed.group(1) if installed else "")


def summarise(alerts: list[dict]) -> dict:
    """Group the open Trivy alerts by image, splitting fixable from not.

    Only open alerts and only Trivy's count: a dismissed alert is a decision
    already taken, and the Scorecard findings in the same tab are not
    vulnerabilities at all — mixing them in would inflate every number here.
    """
    per_image: dict = collections.defaultdict(
        lambda: {
            "open": 0,
            "fixable": 0,
            "fixable_by_severity": collections.Counter(),
            "packages": collections.defaultdict(set),
        }
    )
    for alert in alerts:
        if alert.get("state") != "open":
            continue
        if ((alert.get("tool") or {}).get("name")) != "Trivy":
            continue
        bucket = per_image[image_of(alert)]
        bucket["open"] += 1
        fix = fixed_version(alert)
        if not fix:
            continue
        bucket["fixable"] += 1
        bucket["fixable_by_severity"][severity_of(alert)] += 1
        name, installed = package_of(alert)
        if name:
            bucket["packages"][f"{name} {installed}".strip()].add(fix)
    return dict(per_image)


def render(summary: dict) -> str:
    """The report, worst-affected image first."""
    if not summary:
        return "No open Trivy alerts.\n"

    lines = []
    order = sorted(summary.items(), key=lambda kv: (-kv[1]["fixable"], kv[0]))
    width = max(len(name) for name in summary)
    lines.append(
        f"{'image'.ljust(width)}  {'open':>5}  {'fixable':>7}  severity of the fixable"
    )
    for name, bucket in order:
        counts = bucket["fixable_by_severity"]
        detail = (
            ", ".join(f"{counts[s]} {s}" for s in SEVERITY_ORDER if counts[s]) or "—"
        )
        open_count = bucket["open"]
        fixable = bucket["fixable"]
        lines.append(f"{name.ljust(width)}  {open_count:>5}  {fixable:>7}  {detail}")

    total_open = sum(b["open"] for b in summary.values())
    total_fixable = sum(b["fixable"] for b in summary.values())
    lines.append("")
    lines.append(
        f"{total_fixable} of {total_open} open Trivy alerts have a fix published "
        "upstream."
    )
    # Deliberately not "so rebuild the image": true for a Debian package, which
    # `apt-get upgrade` picks up on the next build, and false for one baked into
    # a base image this repository does not build — the backend's urllib3 and
    # msgpack sit inside jauderho/yt-dlp, and the daily refresh has rebuilt on
    # the newest base every day without clearing them. Which of the two a row is
    # depends on where the package lives, and the scanner does not say.
    lines.append(
        "A rebuild clears the ones whose package the image's own build installs; "
        "the rest wait on the base image that carries them."
    )

    for name, bucket in order:
        if not bucket["packages"]:
            continue
        lines.append("")
        lines.append(f"{name} — packages with a fix available:")
        for package in sorted(bucket["packages"]):
            fixes = ", ".join(sorted(bucket["packages"][package]))
            lines.append(f"  {package} -> {fixes}")
    return "\n".join(lines) + "\n"


def fetch_alerts(repo: str, token: str) -> list[dict]:
    """Every code-scanning alert, following pagination to the end.

    Not `state=open` as a query parameter: the filter is applied in
    `summarise` so that a future caller can ask a different question of the
    same payload without a second round of requests.
    """
    alerts: list[dict] = []
    page = 1
    while True:
        url = f"{API.format(repo=repo)}?per_page={PER_PAGE}&page={page}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "content-security-alert-summary",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                batch = json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:200]
            fail(f"GitHub returned {error.code} for {url}: {detail}")
        except urllib.error.URLError as error:
            fail(f"could not reach GitHub: {error.reason}")
        if not batch:
            return alerts
        alerts.extend(batch)
        page += 1


def main() -> None:
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        fail(
            "no token: set GH_TOKEN (GH_TOKEN=$(gh auth token) works) — reading "
            "code-scanning alerts needs the `security_events` or `repo` scope"
        )
    print(render(summarise(fetch_alerts(REPO, token))), end="")


if __name__ == "__main__":
    main()
