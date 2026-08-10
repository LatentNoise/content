"""Guard-rails for the Chromium browser extension — the first non-Python client (ADR 0016).

The extension cannot use `content_sdk`: it is JavaScript, so it speaks `/api/v1`
directly. That puts it outside every check the Python consumers get, including
`test_layering.py`, which scans `.py` only and would therefore wave it through
in silence rather than fail.

These tests close that gap in two layers:

**Always, in pure Python** — the request bodies the extension emits are
validated against the **real** `GenerationRequest`, so contract drift fails
`make validate`; the manifest is checked for Manifest V3 and for permission
minimality, because an over-broad host permission ships once and is never
noticed again; and `request.js` is read for contract fields the API lacks.

**When `node` happens to be installed** — the two pure modules (`lib/url.js`,
`lib/request.js`) are actually *executed*, so the URL table below tests the
JavaScript rather than testing that Python agrees with itself. This skips
cleanly without node, the same discipline the suite already uses for `pdftotext`
and `typst`: no Node toolchain is required, and none is added to the repository.

What none of this proves is the extension **running in a browser** — the popup,
the service worker's CORS exemption, the permission flow. That is stated plainly
in `apps/browser-extension-chromium/README.md` rather than left to be assumed.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
EXTENSION = REPO / "apps" / "browser-extension-chromium"
MANIFEST = EXTENSION / "manifest.json"

# The engine's own model, so there is one definition of the contract.
sys.path.insert(0, str(REPO / "apps" / "backend"))


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


# --- the extension is really in the repository ----------------------------------


def test_no_extension_file_is_hidden_by_the_allowlist():
    """The allowlist `.gitignore` blocks every web format by default.

    An extension git refuses to track would be missing from every clone while
    looking perfectly fine here — the failure that hid the Typst template for
    weeks (D-41), applied to a whole application.

    Asks `git check-ignore` rather than `git ls-files`: the hazard is a file the
    allowlist *forbids*, which is permanent, not one that merely has not been
    staged yet, which is just work in progress.
    """
    import subprocess

    on_disk = sorted(
        str(path.relative_to(REPO))
        for path in EXTENSION.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )
    assert on_disk, "the extension has no files"

    # check-ignore exits 0 when at least one path is ignored, 1 when none are.
    result = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=REPO,
        input="\n".join(on_disk),
        capture_output=True,
        text=True,
        check=False,  # exit 1 simply means "nothing is ignored", which is the pass
    )
    ignored = sorted(line for line in result.stdout.split("\n") if line.strip())
    assert not ignored, (
        f"the allowlist in .gitignore hides {ignored} — they can never be "
        "committed, so every clone would be missing them"
    )


def test_new_extension_files_are_committable():
    """The scan above has a blind spot the 2026-08-10 rename exposed: git
    suppresses ignore rules for *tracked* paths, so once the files are in the
    index, `check-ignore` answers "not ignored" even when the allowlist rule
    rotted (the negations still said `apps/browser-extension/**` after the
    directory became `apps/browser-extension-chromium/`). Tracked files kept
    working; every NEW file silently became uncommittable.

    So probe with paths that do not exist: for each runtime format the
    extension is made of, an untracked candidate must be committable."""
    import subprocess

    candidates = [
        f"{EXTENSION.relative_to(REPO)}/somewhere/new-file{suffix}"
        for suffix in (".js", ".json", ".html", ".css", ".png")
    ]
    result = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=REPO,
        input="\n".join(candidates),
        capture_output=True,
        text=True,
        check=False,
    )
    blocked = sorted(line for line in result.stdout.split("\n") if line.strip())
    assert not blocked, (
        f"the allowlist no longer covers this app's runtime formats: {blocked} "
        "— any new extension file would be silently uncommittable"
    )


# --- the manifest ----------------------------------------------------------------


def test_manifest_is_manifest_v3_with_a_module_service_worker():
    manifest = _manifest()
    assert manifest["manifest_version"] == 3
    background = manifest["background"]
    # A module worker is what lets the extension share `lib/` with the popup
    # without a bundler.
    assert background["type"] == "module"
    assert (EXTENSION / background["service_worker"]).is_file()


def test_manifest_permissions_stay_minimal():
    """An over-broad host permission ships once and is never noticed again.

    `<all_urls>` and wildcard hosts are refused in `host_permissions`; they are
    allowed in `optional_host_permissions`, which grants nothing until the user
    explicitly agrees to a specific origin.
    """
    manifest = _manifest()
    assert set(manifest["permissions"]) <= {"storage", "activeTab"}, (
        "the extension needs no other API permission; adding one is a decision"
    )
    for pattern in manifest["host_permissions"]:
        assert pattern != "<all_urls>", "never ship blanket host access"
        assert not pattern.startswith("http://*"), f"wildcard host granted: {pattern}"
        assert not pattern.startswith("https://*"), f"wildcard host granted: {pattern}"


def test_manifest_declares_every_file_it_points_at():
    """A missing icon or page is a load-time error in Chrome, and the only way
    to see it is to load the extension — so check it here instead."""
    manifest = _manifest()
    referenced = [
        manifest["action"]["default_popup"],
        manifest["options_ui"]["page"],
        *manifest["icons"].values(),
        *manifest["action"]["default_icon"].values(),
    ]
    missing = [path for path in referenced if not (EXTENSION / path).is_file()]
    assert not missing, f"the manifest points at files that do not exist: {missing}"


def test_icons_are_png_because_chrome_refuses_svg():
    manifest = _manifest()
    for size, path in manifest["icons"].items():
        assert path.endswith(".png"), f"icon {size} is not a PNG"
        # The 8-byte PNG signature: a renamed SVG would pass the suffix check.
        assert (EXTENSION / path).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


# --- the contract ----------------------------------------------------------------


# `_states.json` is the input that generates the others, not a request body.
FIXTURES = sorted(
    path
    for path in (EXTENSION / "fixtures").glob("*.json")
    if not path.name.startswith("_")
)


def test_there_are_fixtures_to_check():
    assert FIXTURES, "the contract check is vacuous without request fixtures"


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.stem)
def test_every_request_the_extension_builds_is_valid(fixture):
    """The bodies `lib/request.js` emits, validated by the engine's own model.

    `GenerationRequest` forbids unknown fields, so this catches both a field the
    API does not have and one whose name drifted. These fixtures are the
    reviewed record of what the extension sends: change `buildRequest`, change
    them here.
    """
    from content.domain.request import GenerationRequest
    from content.domain.validation import validate_structure

    payload = json.loads(fixture.read_text(encoding="utf-8"))
    request = GenerationRequest.model_validate(payload)

    result = validate_structure(request)
    assert result.valid, [issue.model_dump() for issue in result.errors]


def test_the_extension_never_sends_a_reserved_field():
    """Reserved fields are refused (`option_not_supported`), so sending one
    would turn every download into a 422. The extension omits `execution`,
    `preferences` and `constraints` entirely; this proves it stays that way."""
    from content.domain.reserved import RESERVED_PATHS

    top_level_blocks = {path.split(".")[0] for path in RESERVED_PATHS}
    for fixture in FIXTURES:
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        present = top_level_blocks & set(payload)
        assert not present, f"{fixture.name} sends reserved block(s): {sorted(present)}"


def test_request_builder_mentions_no_field_the_contract_lacks():
    """A cheap read of `lib/request.js` for object keys that look like contract
    fields but are not — the drift a fixture would only catch once someone
    remembered to add one."""
    from content.domain.request import (
        BaseOutput,
        BaseSource,
        GenerationRequest,
        VideoOptions,
        VideoSelection,
    )

    known = set()
    for model in (
        GenerationRequest,
        BaseSource,
        BaseOutput,
        VideoOptions,
        VideoSelection,
    ):
        known |= set(model.model_fields)
    # Field names the builder writes into the request body, as `key:` literals.
    source = (EXTENSION / "lib" / "request.js").read_text(encoding="utf-8")
    body = source.split("export function buildRequest", 1)[-1]
    written = set(re.findall(r"^\s{4,}([a-z_]+):", body, re.MULTILINE))
    # Local helpers and non-contract keys used inside the function.
    written -= {"label", "outputs_", "state"}
    unknown = {name for name in written if name not in known}
    assert not unknown, (
        f"`buildRequest` writes {sorted(unknown)}, which no contract model "
        "declares — the API would reject it"
    )


def test_the_popup_names_the_engine_it_talks_to() -> None:
    """The footer answering "where is this sending my video?" stays wired.

    A static guard in the suite's usual idiom: the popup carries the element
    and the script fills it from the configured backend. Whether it *renders*
    is browser-only, like everything else about the popup (module docstring).
    """
    html = (EXTENSION / "popup" / "popup.html").read_text(encoding="utf-8")
    assert 'id="engine-target"' in html, "the popup lost its engine footer"
    script = (EXTENSION / "popup" / "popup.js").read_text(encoding="utf-8")
    filling = script.split("engine-target", 1)[-1]
    assert "backendUrl" in filling, (
        "the engine footer is no longer filled from settings.backendUrl"
    )


# --- executing the JavaScript, when a runtime happens to be available ------------

NODE = shutil.which("node")
node_required = pytest.mark.skipif(
    NODE is None, reason="node is not installed; the JS logic is not executed here"
)

# The specification of record for `lib/url.js`. Kept here, in the language the
# gate is written in, so the rules are reviewed with the contract rather than
# buried in a browser.
URL_CASES = [
    # (tab address, expected source uri, expected kind)
    (
        "https://www.youtube.com/watch?v=abc123",
        "https://www.youtube.com/watch?v=abc123",
        "video",
    ),
    ("https://youtu.be/abc123", "https://www.youtube.com/watch?v=abc123", "video"),
    (
        "https://www.youtube.com/shorts/abc123",
        "https://www.youtube.com/watch?v=abc123",
        "video",
    ),
    (
        "https://www.youtube.com/embed/abc123",
        "https://www.youtube.com/watch?v=abc123",
        "video",
    ),
    # The trap: a video playing *inside* a playlist is still that video.
    (
        "https://www.youtube.com/watch?v=abc123&list=PL999",
        "https://www.youtube.com/watch?v=abc123",
        "video",
    ),
    # A playlist page really is the collection.
    (
        "https://www.youtube.com/playlist?list=PL999",
        "https://www.youtube.com/playlist?list=PL999",
        "collection",
    ),
    # Tracking noise never reaches the engine (it would pollute the cache key).
    (
        "https://youtu.be/abc123?si=TRACKER",
        "https://www.youtube.com/watch?v=abc123",
        "video",
    ),
    # A timestamp is meaning, not noise, so it survives.
    (
        "https://www.youtube.com/watch?v=abc123&t=42",
        "https://www.youtube.com/watch?v=abc123&t=42",
        "video",
    ),
    # Not YouTube: passed through. yt-dlp supports 1000+ sites and the
    # extension must never decide it knows better.
    ("https://vimeo.com/12345", "https://vimeo.com/12345", "unknown"),
    # Not a source at all.
    ("chrome://extensions", "", "unsupported"),
    ("file:///tmp/x.mp4", "", "unsupported"),
]


def _run_node(script: str) -> object:
    import subprocess

    result = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        cwd=EXTENSION,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,  # the assertion below reports node's stderr, which is useful
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@node_required
def test_url_normalisation_matches_the_specification():
    """Runs the real `lib/url.js` over the table above.

    Without this the Python table would only assert that Python agrees with
    itself. Skips cleanly when node is absent, the same discipline the suite
    already uses for pdftotext and typst.
    """
    inputs = json.dumps([case[0] for case in URL_CASES])
    got = _run_node(
        "import {normalizeSourceUrl} from './lib/url.js';"
        f"const out = {inputs}.map((u) => normalizeSourceUrl(u));"
        "console.log(JSON.stringify(out));"
    )
    for (raw, expected_uri, expected_kind), actual in zip(URL_CASES, got):
        assert actual["uri"] == expected_uri, f"{raw} -> {actual['uri']}"
        assert actual["kind"] == expected_kind, f"{raw} -> {actual['kind']}"


@node_required
def test_every_fixture_is_what_the_builder_really_emits():
    """Closes the loop the fixtures leave open.

    A fixture on its own only proves *some* valid body exists. Regenerating each
    one from `fixtures/_states.json` through the real `buildRequest` proves it is
    the body the extension actually sends — otherwise the two drift the moment
    someone edits one and forgets the other.
    """
    from content.domain.request import GenerationRequest

    states = json.loads((EXTENSION / "fixtures" / "_states.json").read_text("utf-8"))
    built = _run_node(
        "import {buildRequest} from './lib/request.js';"
        f"const states = {json.dumps(states)};"
        "const out = {};"
        "for (const [name, state] of Object.entries(states)) out[name] = buildRequest(state);"
        "console.log(JSON.stringify(out));"
    )
    assert set(built) == {path.stem for path in FIXTURES}, (
        "every state must have a committed fixture, and vice versa"
    )
    for name, body in built.items():
        committed = json.loads(
            (EXTENSION / "fixtures" / f"{name}.json").read_text("utf-8")
        )
        assert body == committed, f"{name}.json is stale — regenerate it"
        GenerationRequest.model_validate(body)


@node_required
def test_no_intent_sends_no_delivery_block():
    """ADR 0018 flipped the burden: the *server* delivers by default and names
    the file after the video (ADR 0017), so a request with neither folder nor
    filename must carry no `delivery` at all — the engine decides. What the
    user did set is passed through raw; the server sanitizes, never rejects."""
    state = {
        "uri": "https://www.youtube.com/watch?v=x",
        "outputs": ["video"],
        "maxHeight": None,
        "container": "",
        "subtitleLangs": [],
        "folder": "",
        "filename": "",
        "credentialId": "",
    }
    body = _run_node(
        "import {buildRequest} from './lib/request.js';"
        f"console.log(JSON.stringify(buildRequest({json.dumps(state)})));"
    )
    assert "delivery" not in body["outputs"][0]

    body = _run_node(
        "import {buildRequest} from './lib/request.js';"
        f"console.log(JSON.stringify(buildRequest({json.dumps({**state, 'folder': 'Tech'})})));"
    )
    assert body["outputs"][0]["delivery"] == {"folder": "Tech"}


def test_a_slashed_title_is_the_servers_problem_now():
    """The extension used to carry its own sanitizer (`lib/filename.js`)
    because the engine rejected path separators (D-51). The engine now
    sanitizes name intent itself, so the client-side copy is gone — this
    proves the contract really does accept the title that used to 422."""
    from content.domain.request import Delivery

    assert not (EXTENSION / "lib" / "filename.js").exists(), (
        "the client-side sanitizer is back — the server owns sanitization"
    )
    delivery = Delivery(folder="", filename="Artist - Song / Official Video")
    assert delivery.filename == "Artist - Song - Official Video"


@node_required
def test_the_builder_refuses_subtitles_without_a_language():
    """The contract requires a non-empty list, so the client fails fast rather
    than letting the API reject the whole job."""
    state = {
        "uri": "https://www.youtube.com/watch?v=x",
        "outputs": ["subtitles"],
        "subtitleLangs": [],
        "folder": "",
        "credentialId": "",
        "maxHeight": None,
    }
    outcome = _run_node(
        "import {buildRequest} from './lib/request.js';"
        "let error = '';"
        f"try {{ buildRequest({json.dumps(state)}); }} catch (e) {{ error = e.message; }}"
        "console.log(JSON.stringify({error}));"
    )
    assert "language" in outcome["error"], outcome


# --- the release zip (what a fresh user actually downloads) ----------------------


def test_packaged_zip_is_a_complete_unpacked_extension(tmp_path):
    """`make extension-zip`, then act like the user: extract it and check that
    the folder handed to "Load unpacked" is complete and contains nothing else.

    Complete: `manifest.json` at the root, and every file the manifest or an
    HTML page references resolves inside the extraction. Nothing else: only
    the runtime entries — a README or the contract fixtures in a release zip
    would be dead weight at best and a source of confusion at worst.
    """
    import subprocess
    import zipfile

    subprocess.run(
        ["make", "extension-zip", f"EXT_ZIP_DIR={tmp_path}"],
        cwd=REPO,
        check=True,
        capture_output=True,
    )
    archives = list(tmp_path.glob("content-browser-extension-chromium-v*.zip"))
    assert len(archives) == 1, f"expected exactly one archive, got {archives}"
    archive = archives[0]

    manifest_version = _manifest()["version"]
    assert archive.name == (
        f"content-browser-extension-chromium-v{manifest_version}.zip"
    ), "the asset name must carry the manifest's own version"

    unpacked = tmp_path / "unpacked"
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(unpacked)

    # The root the user selects: manifest.json directly inside, and only the
    # runtime entries beside it.
    assert (unpacked / "manifest.json").is_file(), "manifest.json must sit at the root"
    entries = sorted(path.name for path in unpacked.iterdir())
    assert entries == sorted(
        ["manifest.json", "background", "icons", "lib", "options", "popup"]
    ), f"unexpected zip layout: {entries}"

    # Everything the manifest points at exists in the extraction.
    manifest = json.loads((unpacked / "manifest.json").read_text(encoding="utf-8"))
    referenced = [
        manifest["background"]["service_worker"],
        manifest["action"]["default_popup"],
        manifest["options_ui"]["page"],
        *manifest["icons"].values(),
        *manifest["action"]["default_icon"].values(),
    ]
    missing = [path for path in referenced if not (unpacked / path).is_file()]
    assert not missing, f"the packaged manifest points at missing files: {missing}"

    # Everything the HTML pages load exists too (scripts, styles, images).
    for page in ("popup/popup.html", "options/options.html"):
        html = (unpacked / page).read_text(encoding="utf-8")
        for target in re.findall(r'(?:src|href)="([^"]+)"', html):
            if target.startswith(("http:", "https:", "#", "data:")):
                continue
            resolved = (unpacked / page).parent / target
            assert resolved.is_file(), f"{page} references a missing file: {target}"

    # And the ES module graph resolves: every relative static import lands on
    # a packaged file (the popup and worker share lib/ without a bundler).
    for module in unpacked.rglob("*.js"):
        source = module.read_text(encoding="utf-8")
        for target in re.findall(
            r'^\s*(?:import|export)\s[^;]*?from\s+["\'](\.[^"\']+)["\']',
            source,
            re.MULTILINE,
        ):
            resolved = (module.parent / target).resolve()
            assert resolved.is_file(), f"{module.name} imports a missing {target}"
