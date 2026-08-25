"""Is the pinned jauderho/yt-dlp base image still the current one?

Reads the pin out of the Dockerfile, asks Docker Hub what `latest` resolves to,
and — only when they differ — writes the body of a tracking issue. It changes
nothing else: no bump, no build, no pull request. See
docs/operations/ytdlp-base-image.md for what the maintainer does with it.

Standard library only, so the workflow needs no install step and this stays
runnable by hand:

    python3 .github/scripts/ytdlp_base_check.py

Exits 0 whether or not an update exists — "upstream moved" is a finding, not a
failure. It exits non-zero only when the check itself could not be carried out
(an unparseable Dockerfile, an unreachable registry), because a check that
quietly reports "all current" when it never ran is worse than a red job.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request

IMAGE = "jauderho/yt-dlp"
REGISTRY = "https://registry-1.docker.io/v2"
AUTH = "https://auth.docker.io/token?service=registry.docker.io&scope=repository:{image}:pull"
HUB_TAGS = "https://hub.docker.com/v2/repositories/{image}/tags?page_size=50&ordering=last_updated"
RELEASE_URL = "https://github.com/yt-dlp/yt-dlp/releases/tag/{version}"

# Both the OCI and the legacy Docker media types, index first: the tag is a
# multi-arch index and we want the index digest, not one platform's.
MANIFEST_ACCEPT = (
    "application/vnd.oci.image.index.v1+json, "
    "application/vnd.docker.distribution.manifest.list.v2+json, "
    "application/vnd.oci.image.manifest.v1+json, "
    "application/vnd.docker.distribution.manifest.v2+json"
)

DOCKERFILE = os.getenv("DOCKERFILE", "apps/backend/Dockerfile")
TIMEOUT = 30


def fail(message: str) -> "None":
    print(f"::error::{message}", file=sys.stderr)
    raise SystemExit(1)


def _get(url: str, headers: "dict[str, str] | None" = None, head: bool = False):
    request = urllib.request.Request(url, headers=headers or {})
    if head:
        request.get_method = lambda: "HEAD"
    return urllib.request.urlopen(request, timeout=TIMEOUT)


def read_pin(path: str) -> "tuple[str, str]":
    """The version and digest currently pinned in the Dockerfile."""
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as error:
        fail(f"cannot read {path}: {error}")
    version = re.search(r"^ARG\s+YTDLP_BASE_VERSION=(\S+)", text, re.MULTILINE)
    digest = re.search(
        r"^ARG\s+YTDLP_BASE_DIGEST=(sha256:[0-9a-f]{64})", text, re.MULTILINE
    )
    if not version or not digest:
        fail(
            f"{path} does not declare both ARG YTDLP_BASE_VERSION and "
            "ARG YTDLP_BASE_DIGEST — the pin format changed, so this check "
            "cannot tell what is pinned."
        )
    return version.group(1), digest.group(1)


def upstream_digest() -> str:
    """What `latest` resolves to right now, straight from the registry."""
    try:
        token = json.load(_get(AUTH.format(image=IMAGE)))["token"]
        response = _get(
            f"{REGISTRY}/{IMAGE}/manifests/latest",
            headers={"Authorization": f"Bearer {token}", "Accept": MANIFEST_ACCEPT},
            head=True,
        )
    except (urllib.error.URLError, KeyError, ValueError, OSError) as error:
        fail(f"could not reach the registry: {error}")
    digest = response.headers.get("Docker-Content-Digest", "")
    if not digest.startswith("sha256:"):
        fail("the registry returned no Docker-Content-Digest for :latest")
    return digest


def version_for(digest: str) -> str:
    """The human-readable tag sharing that digest, e.g. `2026.07.04`.

    Best-effort: the digest is the authority, and an unnamed candidate is still
    reportable. Returns "" when the Hub API is unhelpful.
    """
    try:
        tags = json.load(_get(HUB_TAGS.format(image=IMAGE))).get("results", [])
    except (urllib.error.URLError, ValueError, OSError):
        return ""
    dated = [t for t in tags if re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", t.get("name", ""))]
    for tag in dated:
        if tag.get("digest") == digest:
            return tag["name"]
    return ""


def issue_title(pinned_version: str, new_version: str, new_digest: str) -> str:
    """Say which of the two very different things happened.

    A moving digest is worth watching either way, but the first version of this
    title reported both as "X available (pinned: X)" — which, when the tag had
    merely been rebuilt, read as a bug in the checker and buried the case that
    actually matters. yt-dlp going stale is what breaks YouTube downloads; a
    rebuilt base is usually distro patches.
    """
    if not new_version:
        return (
            f"yt-dlp base image: untagged {new_digest[:19]} (pinned: {pinned_version})"
        )
    if new_version == pinned_version:
        return f"yt-dlp base image rebuilt: {new_version} republished, same yt-dlp"
    return f"yt-dlp {new_version} available (pinned: {pinned_version})"


def issue_body(pinned_version, pinned_digest, new_version, new_digest) -> str:
    named = new_version or "(untagged — compare by digest)"
    release = (
        f"[yt-dlp {new_version}]({RELEASE_URL.format(version=new_version)})"
        if new_version
        else "_no matching version tag found; check "
        "https://github.com/yt-dlp/yt-dlp/releases_"
    )
    if new_version and new_version == pinned_version:
        headline = (
            f"`{IMAGE}:{new_version}` has been **rebuilt**: same yt-dlp, new "
            "image. Typically distro patches in the base layers — worth taking, "
            "rarely urgent."
        )
    elif new_version:
        headline = (
            f"**yt-dlp {new_version}** is out; the pin is still "
            f"`{pinned_version}`. This is the one that matters: a stale yt-dlp "
            "is how YouTube downloads start failing."
        )
    else:
        headline = (
            f"A newer `{IMAGE}` base image is available, with no version tag "
            "matching its digest."
        )
    return f"""\
{headline} **Nothing has been changed** — this issue is a notification, and the
bump is a deliberate, validated act.

| | Pinned now | Available |
| --- | --- | --- |
| Version | `{pinned_version}` | `{named}` |
| Digest | `{pinned_digest}` | `{new_digest}` |

Upstream release: {release}
Base image: https://hub.docker.com/r/{IMAGE}/tags

## Before accepting the bump

Run this locally — it is the whole gate. Do not commit until every step passes.

```bash
# 1. Update the pin in apps/backend/Dockerfile
#      ARG YTDLP_BASE_VERSION={new_version or "<version>"}
#      ARG YTDLP_BASE_DIGEST={new_digest}

# 2. Build the backend image on the new base
docker compose build content

# 3. The hermetic gate
make validate

# 4. The end-to-end release checks
make validate-release

# 5. The real yt-dlp media slice must actually run, not skip.
#    `make validate-release` prints its coverage; confirm the line reads
#    [x] yt-dlp media source

# 6. Inspect the yt-dlp the new image really carries
docker compose run --rm --entrypoint yt-dlp content --version

# 7. Commit the bump only if all of the above succeeded, quoting the
#    version this issue names and the yt-dlp version step 6 reported.
```

Close this issue once the bump is committed. If the check still sees a newer
image next Monday, it will update this issue in place rather than open another.

<sub>Filed by `.github/workflows/ytdlp-base-check.yml`. Procedure:
`docs/operations/ytdlp-base-image.md`.</sub>
"""


def main() -> None:
    pinned_version, pinned_digest = read_pin(DOCKERFILE)
    new_digest = upstream_digest()

    print(f"pinned   : {pinned_version}  {pinned_digest}")
    print(f"upstream : latest            {new_digest}")

    up_to_date = new_digest == pinned_digest
    new_version = "" if up_to_date else version_for(new_digest)

    if up_to_date:
        print("Pinned base is current — no issue filed.")
    else:
        print(f"Update available: {new_version or '(untagged)'}")
        with open("issue-body.md", "w", encoding="utf-8") as body:
            body.write(
                issue_body(pinned_version, pinned_digest, new_version, new_digest)
            )

    title = issue_title(pinned_version, new_version, new_digest)
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"update_available={str(not up_to_date).lower()}\n")
            handle.write(f"issue_title={title}\n")
            # What the refresh workflow builds with. The digest is the
            # authority — it is what Docker resolves and it is immutable — and
            # the version is carried alongside so the rebuilt image can still
            # say, in a label a human reads, which yt-dlp is inside it.
            handle.write(f"pinned_version={pinned_version}\n")
            handle.write(f"new_version={new_version}\n")
            handle.write(f"new_digest={new_digest}\n")


if __name__ == "__main__":
    main()
