"""The summary must separate "nobody can fix this" from "rebuild the image".

`work/NEXT.md` told its readers, for sixteen days, to stop reading about the
Trivy alerts: *"every one inspected reports an empty Fixed Version … so there
is nothing to decide and nothing to do"*. On 2026-10-14 that was false — 138 of
the 630 open alerts carried a published fix, 17 of them HIGH — and the reason
nobody noticed is that re-establishing it meant hand-querying the API.

These tests hold the two properties that make the re-check trustworthy: an
alert with no `Fixed Version` is never counted as fixable, and alerts are
grouped by the image they were scanned from rather than by the path they
happen to carry. The second one is not hypothetical — grouping by path is what
a night pass did first, and it hid the backend image's findings entirely.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / ".github/scripts"))

from security_alert_summary import (  # noqa: E402  — the path insert above must run first
    fixed_version,
    package_of,
    render,
    severity_of,
    summarise,
)


def alert(
    *,
    image="trivy-content",
    path="Python",
    state="open",
    tool="Trivy",
    severity="high",
    cvss=True,
    package="urllib3",
    installed="2.7.0",
    fixed="2.8.0",
):
    """One alert shaped the way GitHub returns Trivy's SARIF."""
    message = f"Package: {package}\nInstalled Version: {installed}\n"
    message += "Vulnerability CVE-2026-97689\nSeverity: HIGH\n"
    if fixed is not None:
        message += f"Fixed Version: {fixed}\n"
    message += "Link: [CVE-2026-97689](https://avd.aquasec.com/nvd/cve-2026-97689)"
    rule = {"id": "CVE-2026-97689"}
    if cvss:
        rule["security_severity_level"] = severity
    else:
        rule["severity"] = severity
    return {
        "state": state,
        "tool": {"name": tool},
        "rule": rule,
        "most_recent_instance": {
            "category": image,
            "location": {"path": path, "start_line": 1},
            "message": {"text": message},
        },
    }


def test_an_alert_with_no_published_fix_is_not_counted_as_fixable():
    """The whole point: an unfixable CVE is nobody's work.

    Counting it as actionable is what turns a security tab into noise.
    """
    summary = summarise([alert(fixed=None), alert(fixed=None), alert()])
    assert summary["trivy-content"]["open"] == 3
    assert summary["trivy-content"]["fixable"] == 1


def test_an_empty_fixed_version_field_counts_as_no_fix():
    """Trivy omits the line entirely, but a blank value must not read as a version."""
    assert fixed_version(alert(fixed="")) == ""
    assert summarise([alert(fixed="")])["trivy-content"]["fixable"] == 0


def test_alerts_are_grouped_by_scanned_image_not_by_path():
    """The defect this guards: one image's findings carry several different paths.

    An OS-package finding is pathed at the image name, a Python-package finding
    in the same image at `Python`. Grouping by path splits the image in two and
    makes the backend's six findings look like a different target's.
    """
    summary = summarise(
        [
            alert(image="trivy-content", path="Python"),
            alert(image="trivy-content", path="latentnoise/content"),
            alert(image="trivy-content-studio", path="latentnoise/content-studio"),
        ]
    )
    assert summary["trivy-content"]["open"] == 2
    assert summary["trivy-content-studio"]["open"] == 1


def test_only_open_trivy_alerts_count():
    """A dismissed alert is a decision taken; a Scorecard finding is not a CVE."""
    summary = summarise(
        [
            alert(),
            alert(state="dismissed"),
            alert(state="fixed"),
            alert(tool="Scorecard"),
        ]
    )
    assert summary["trivy-content"]["open"] == 1
    assert summary["trivy-content"]["fixable"] == 1


def test_severity_falls_back_when_no_cvss_score_is_published():
    """The xz-utils rebuild advisories arrive with `security_severity_level` null."""
    assert severity_of(alert(severity="high", cvss=True)) == "high"
    assert severity_of(alert(severity="note", cvss=False)) == "note"
    assert severity_of({"rule": {}}) == "none"


def test_the_affected_package_and_its_fix_are_reported_together():
    """ "Rebuild the image" is only actionable once you can see what it would move."""
    assert package_of(alert(package="msgpack", installed="1.1.2")) == (
        "msgpack",
        "1.1.2",
    )
    summary = summarise([alert(package="msgpack", installed="1.1.2", fixed="1.2.1")])
    assert summary["trivy-content"]["packages"]["msgpack 1.1.2"] == {"1.2.1"}


def test_the_report_names_the_rebuild_and_counts_both_halves():
    """A reader must be able to act on the output without opening the Security tab."""
    report = render(
        summarise(
            [
                alert(image="trivy-content-studio", fixed="5.8.1-1+deb13u2"),
                alert(image="trivy-content-studio", fixed=None),
                alert(image="trivy-content", fixed="2.8.0"),
            ]
        )
    )
    assert "1 of 2 open" not in report  # not per-image totals
    assert "2 of 3 open Trivy alerts have a fix published upstream" in report
    assert "A rebuild clears the ones" in report
    assert "trivy-content-studio" in report and "trivy-content" in report


def test_no_alerts_at_all_says_so_rather_than_printing_an_empty_table():
    assert render(summarise([])) == "No open Trivy alerts.\n"
