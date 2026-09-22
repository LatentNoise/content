"""UI non-regression: the three apps must render, and their dynamism must hold —
what a source lets you do is driven by the resolved capabilities (ADR 0013).

Hermetic: a fake client (see conftest) supplies canned contract-shaped answers;
no backend, no network.
"""


def _all_text(at) -> str:
    parts = []
    for kind in ("markdown", "caption", "info", "warning", "error"):
        for el in getattr(at, kind):
            parts.append(getattr(el, "value", "") or "")
    return " ".join(parts)


def _labels(at, kind) -> list[str]:
    return [getattr(el, "label", "") or "" for el in getattr(at, kind)]


def _generation_request(at) -> dict:
    """The body the page says it will send (its `st.json` preview) — the same
    dict `build_request()` produced, so a test can assert on real intent."""
    import json as _json

    value = at.json[0].value
    return _json.loads(value) if isinstance(value, str) else value


# --- HomeTube ------------------------------------------------------------------


def test_hometube_video_offers_the_derived_outputs(run_app):
    at = run_app("hometube", "https://x/video")
    assert not at.exception, at.exception
    labels = _labels(at, "checkbox")
    assert any("Video" in x for x in labels)
    assert any("Transcript" in x for x in labels)  # derivable, offered
    assert any("Summary" in x for x in labels)


def test_hometube_audio_source_hides_video(run_app):
    at = run_app("hometube", "https://x/audio-track")
    assert not at.exception, at.exception
    labels = _labels(at, "checkbox")
    assert any("Audio" in x for x in labels)
    # Video is NOT offered for a pure-audio source…
    assert not any("🎬 Video" in x for x in labels)
    # …and the blocked outputs are listed with a reason.
    assert "not available for this source" in _all_text(
        at
    ).lower() or "Not available" in _all_text(at)


def test_hometube_language_prefs_drive_the_defaults(run_app):
    """Server prefs (fr primary, en/es secondaries, VO first, primary subs OFF)
    must pre-fill the selectors — never an empty 'Choose options', never every
    track. Fake source: audio en+ja (VO ja), subtitles en/fr manual + de auto."""
    at = run_app("hometube", "https://x/video")
    assert not at.exception, at.exception
    ms = {m.label: m for m in at.multiselect}
    # Audio: VO (ja) then wanted secondaries present (en) — fr/es not offered.
    assert ms["Audio languages"].value == ["ja", "en"]
    # Subtitles: primary fr EXCLUDED (include=false), secondaries ∩ avail = en.
    assert ms["Subtitles"].value == ["en"]


def test_hometube_playlist_uses_each_item_choice(run_app):
    at = run_app("hometube", "https://x/playlist?list=1")
    assert not at.exception, at.exception
    radios = [tuple(r.options) for r in at.radio]
    assert any("🎬 Video" in o and "🎵 Audio only" in o for o in radios)


# --- Content Studio ------------------------------------------------------------


def _studio_analyze(run_app, uri):
    at = run_app("studio")
    assert not at.exception, at.exception
    at.text_input(key="uri-0").set_value(uri).run()
    analyze = [b for b in at.button if "Analyze" in (b.label or "")]
    assert analyze, "Analyze button not found"
    analyze[0].click().run()
    return at


def test_studio_audio_source_blocks_video_output(run_app):
    at = _studio_analyze(run_app, "https://x/audio-track")
    assert not at.exception, at.exception
    text = _all_text(at)
    # the video output is gated off with a reason; audio stays available
    assert "no source can produce this" in text
    assert "audio" in text.lower()


def test_studio_video_source_offers_outputs(run_app):
    at = _studio_analyze(run_app, "https://x/video")
    assert not at.exception, at.exception
    # capability pills / summary mention the producible outputs
    assert "video" in _all_text(at).lower()


# --- Content Console -----------------------------------------------------------


def test_console_renders_without_error(run_app):
    at = run_app("console")
    assert not at.exception, at.exception
    assert "Content Admin" in _all_text(at)


def test_console_says_what_a_job_was_about(run_app, monkeypatch):
    """A job must be recognizable at a glance — outputs ← source in the list
    and the detail, plus the first artifact's name as a human title. All of it
    comes from data the API already returns; the console only renders it."""
    from conftest import FakeContentClient

    request = {
        "schema_version": "1.0",
        "sources": [
            {
                "id": "v",
                "type": "url",
                "uri": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
            }
        ],
        "outputs": [{"id": "a", "type": "audio"}, {"id": "b", "type": "video"}],
    }
    row = {
        "job_id": "job_abcdef123456",
        "status": "succeeded",
        "created_at": "2026-08-09T10:00:00+00:00",
        "started_at": "2026-08-09T10:00:01+00:00",
        "finished_at": "2026-08-09T10:00:30+00:00",
        "failure_policy": "required_only",
        "error": "",
        "cancel_requested": False,
        "retry_of": "",
        "plan_id": "plan_x",
        "request": request,
        # Server-provided human label (first artifact's display name + count):
        # the list row shows it instead of the job id, the detail titles on it.
        "artifact_name": "Me at the zoo.webm",
        "artifact_count": 2,
    }
    monkeypatch.setattr(
        FakeContentClient, "list_jobs", lambda self, limit=30: [row], raising=False
    )
    monkeypatch.setattr(
        FakeContentClient,
        "job",
        lambda self, job_id: {**row, "steps": []},
        raising=False,
    )
    monkeypatch.setattr(
        FakeContentClient,
        "artifacts",
        lambda self, job_id: [
            {
                "id": "art_1",
                "filename": "Me at the zoo.webm",
                "media_type": "audio/webm",
                "size_bytes": 252182,
                "checksum": "sha256:abc",
                "provenance": {"producer": {"operation": "media.acquire_audio"}},
            }
        ],
        raising=False,
    )
    monkeypatch.setattr(
        FakeContentClient,
        "events",
        lambda self, job_id, after_sequence=0: [],
        raising=False,
    )
    monkeypatch.setattr(
        FakeContentClient, "logs", lambda self, job_id: {"logs": {}}, raising=False
    )

    at = run_app("console")
    assert not at.exception, at.exception
    text = _all_text(at)
    assert "audio + video" in text  # the outputs, from the request
    assert "youtube.com/watch?v=jNQXAC9IVRw" in text  # the source, scheme stripped
    assert "Me at the zoo" in text  # the artifact as the human title


def test_hometube_shows_which_cookie_file_is_in_use(run_app, monkeypatch):
    """ "Are my cookies actually used?" — the expander answers with the file's
    own facts: path, presence, freshness. Server metadata only; contents never
    travel."""
    from conftest import FakeContentClient

    base_config = FakeContentClient.config

    def with_credentials(self):
        payload = dict(base_config(self))
        payload["credentials"] = ["youtube"]
        payload["credentials_info"] = [
            {
                "id": "youtube",
                "path": "/config/youtube_cookies.txt",
                "exists": True,
                "size_bytes": 1234,
                "updated_at": "2026-08-07T10:00:00+00:00",
            }
        ]
        return payload

    monkeypatch.setattr(FakeContentClient, "config", with_credentials)
    at = run_app("hometube")
    assert not at.exception, at.exception
    # Select the credential in the Cookie Management expander and rerun.
    auth_boxes = [s for s in at.selectbox if "none" in (s.options or [])]
    box = next(s for s in auth_boxes if "youtube" in s.options)
    box.select("youtube").run()
    text = _all_text(at)
    assert "/config/youtube_cookies.txt" in text
    assert "updated" in text


def test_console_reports_credential_files_and_freshness(run_app, monkeypatch):
    """The credentials card shows each id with its file's path and state —
    including the dangling case (declared in .env, file never dropped)."""
    from conftest import FakeContentClient

    base_system = FakeContentClient.system

    def with_credentials(self):
        payload = dict(base_system(self))
        payload["credentials"] = ["vimeo", "youtube"]
        payload["credentials_info"] = [
            {
                "id": "vimeo",
                "path": "/config/vimeo_cookies.txt",
                "exists": False,
                "size_bytes": None,
                "updated_at": None,
            },
            {
                "id": "youtube",
                "path": "/config/youtube_cookies.txt",
                "exists": True,
                "size_bytes": 1234,
                "updated_at": "2026-08-07T10:00:00+00:00",
            },
        ]
        return payload

    monkeypatch.setattr(FakeContentClient, "system", with_credentials)
    at = run_app("console")
    assert not at.exception, at.exception
    text = _all_text(at)
    assert "/config/youtube_cookies.txt" in text
    assert "updated" in text
    assert "file not found" in text  # the dangling declaration is visible


def test_hometube_flags_a_declared_but_missing_cookie_file(run_app, monkeypatch):
    """The default deployment declares the youtube credential before the file
    exists — deliberately. The UI must turn that into guided setup (what to
    drop, where, then what to run), visible without selecting anything."""
    from conftest import FakeContentClient

    base_config = FakeContentClient.config

    def with_missing_credential(self):
        payload = dict(base_config(self))
        payload["credentials"] = ["youtube"]
        payload["credentials_info"] = [
            {
                "id": "youtube",
                "path": "/config/youtube_cookies.txt",
                "exists": False,
                "size_bytes": None,
                "updated_at": None,
            }
        ]
        return payload

    monkeypatch.setattr(FakeContentClient, "config", with_missing_credential)
    at = run_app("hometube")
    assert not at.exception, at.exception
    text = _all_text(at)
    assert "not there yet" in text
    assert "/config/youtube_cookies.txt" in text
    assert "docker-update" in text  # the instruction, not just the alarm


def test_hometube_playlist_still_asks_for_languages(run_app):
    """A playlist's entries are listed, never probed, so there is no track list
    to offer — the selectors used to vanish and the request went out with no
    `audio_languages` and no `embed_subtitles` at all: every downloaded item
    silently lost its subtitles and its extra audio tracks. The preferences now
    stand in as intent (server prefs: primary fr, secondaries en/es, VO first,
    primary excluded from subtitles)."""
    at = run_app("hometube", "https://x/playlist?list=1")
    assert not at.exception, at.exception
    ms = {m.label: m for m in at.multiselect}
    assert "Audio languages" in ms, "a playlist must still let you ask for audio"
    # VO leads, as an unresolved token: the engine expands "original" against
    # each member's own analysis when that member is planned (ADR 0022). This
    # list used to start at "fr", because a playlist had no way to ask for the
    # original voice at all.
    assert ms["Audio languages"].value == ["original", "fr", "en", "es"]
    assert "Subtitles" in ms, "a playlist must still let you ask for subtitles"
    # primary_include_subtitles=false → fr excluded, the secondaries remain.
    # No token here: a subtitle list refuses "original" (it has no meaning for
    # a translated track), which is why the subtitle caller leaves VO off.
    assert ms["Subtitles"].value == ["en", "es"]


def test_hometube_prefills_the_engines_proposed_name(run_app):
    """The name field shows the engine's own proposal (ADR 0017), editable.

    It used to show the raw title as a mere placeholder — a name that is not
    what the file gets called, since the display profile rewrites it (a slash
    becomes " - "). The proposal is the real answer, so the user sees the
    truth and can edit it.
    """
    at = run_app("hometube", "https://x/video")
    assert not at.exception, at.exception
    names = [i for i in at.text_input if (i.label or "").endswith("name")]
    assert names, "the name field disappeared"
    assert names[0].value == "Fake - Official Video"


def test_hometube_sends_no_filename_when_the_proposal_is_untouched(run_app):
    """Leaving the proposal alone is not naming intent: the request carries no
    `delivery.filename`, and the server names the artifacts itself — landing on
    the same name by construction. Editing it does send the raw text."""
    at = run_app("hometube", "https://x/video")
    assert not at.exception, at.exception
    request = _generation_request(at)
    assert "filename" not in (request["outputs"][0].get("delivery") or {})

    names = [i for i in at.text_input if (i.label or "").endswith("name")]
    names[0].set_value("My Own Name").run()
    assert not at.exception, at.exception
    edited = _generation_request(at)
    assert edited["outputs"][0]["delivery"]["filename"] == "My Own Name"


def test_console_access_tab_explains_what_a_key_is(run_app):
    """The Access tab has to say two things plainly, because both surprise
    people: a self-hosted engine has one implicit user and no account, and a
    key cannot be read back after it is created.

    The minting itself is pinned by the engine and SDK suites; AppTest does
    not reach widgets nested inside a tab's form, so this checks what the tab
    tells the reader rather than re-testing the round trip through a fake.
    """
    at = run_app("console")
    assert not at.exception, at.exception

    text = _all_text(at)
    assert "unreadable after creation" in text
    assert "No keys yet." in text
    assert "self-hosted contract, not a missing sign-in" in text


def test_every_surface_resolves_its_visitor_per_request(run_app):
    """The dangerous line, guarded in all three UIs at once.

    `@st.cache_resource` gives one client to the whole process, shared by every
    visitor. The credential must therefore be resolved on each request rather
    than stored on that object — otherwise one person's session becomes the
    next visitor's. This checks the surface actually passes a provider, on all
    three, so adding a fourth cannot quietly skip it.
    """
    from conftest import FakeContentClient

    for surface in ("studio", "console", "hometube"):
        at = run_app(surface)
        assert not at.exception, (surface, at.exception)
        clients = [c for c in FakeContentClient.instances if c.headers_provider]
        assert clients, f"{surface} builds its client without a headers provider"


def test_a_refused_visitor_gets_a_way_in_without_losing_the_page(run_app, monkeypatch):
    """A surface the engine refuses shows the door, above the interface.

    The refusal that matters is **`whoami`**, not some later call. The routes a
    surface boots on carry no owner by design — health, config, the catalog —
    and the owner-scoped ones sit in blocks that degrade to a dash. So a
    visitor whose session was deleted saw a complete, working product that
    silently did nothing. Asking who they are, first, is what makes the refusal
    arrive where it can be acted on.

    The page is not replaced. Someone arriving at a public instance should see
    what the product is before being asked for anything; what they must not do
    is wonder why nothing happens.

    Not a redirect: Streamlit components render inside an iframe sandboxed
    without `allow-top-navigation`, so a script cannot move the browser out of
    the app at all. A button is the whole mechanism, not a fallback.

    All three surfaces, because a fourth must not quietly skip it.
    """
    from conftest import FakeContentClient
    from content_sdk.errors import APIError

    def refuse(self):
        raise APIError(401, {"detail": "Authentication required."})

    monkeypatch.setattr(FakeContentClient, "whoami", refuse, raising=False)

    # The marker is each surface's own brand line, rendered *after* the gate:
    # it proves the script carried on rather than stopping at the banner.
    for surface, title, behind in (
        ("studio", "Content Studio", "every source, every output"),
        ("console", "Content Admin", "backend cockpit"),
        ("hometube", "HomeTube", 'class="ht-brand"'),
    ):
        at = run_app(surface)
        assert not at.exception, (surface, at.exception)
        text = _all_text(at)
        assert "You are not signed in" in text, surface
        assert f"{title} needs to know who you are" in text, surface
        # The way in is a real link to the engine's door, twice: once above the
        # interface, once in the sidebar where it stays after scrolling.
        targets = [b.proto.url for b in at.get("link_button")]
        assert sum("/auth/sign-in" in t for t in targets) == 2, (surface, targets)
        # And the interface itself is still there.
        assert behind in text, surface


def test_a_signed_in_visitor_is_asked_for_nothing(run_app):
    """The self-hosted contract, and the ordinary hosted one: no banner."""
    for surface in ("studio", "console", "hometube"):
        at = run_app(surface)
        assert not at.exception, (surface, at.exception)
        assert "You are not signed in" not in _all_text(at), surface


def test_an_account_can_see_who_it_is_and_leave(run_app, monkeypatch):
    """An account that cannot be left is a defect of signing in, not a missing
    extra: a shared machine, or simply wanting to see what a visitor sees."""
    from conftest import FakeContentClient

    monkeypatch.setattr(
        FakeContentClient,
        "whoami",
        lambda self: {
            "owner_id": "usr_abc",
            "email": "someone@example.com",
            "account": True,
            "is_operator": False,
        },
        raising=False,
    )
    for surface in ("studio", "console", "hometube"):
        at = run_app(surface)
        assert not at.exception, (surface, at.exception)
        assert "someone@example.com" in _all_text(at), surface
        # A link to the engine, not a button that calls it from here: only the
        # browser can drop its own cookie, and only a reconnect refreshes the
        # copy this page captured when its websocket opened (ADR 0034).
        out = [b for b in at.get("link_button") if "/auth/sign-out" in b.proto.url]
        assert len(out) == 1, surface
        assert "Sign out" not in _labels(at, "button"), surface


def test_the_self_hosted_user_is_never_offered_a_way_out(run_app):
    """One implicit user who never signed in (ADR 0030). Offering to sign them
    out would be offering to break their own install."""
    at = run_app("studio")
    assert "Sign out" not in _labels(at, "button")


def test_an_unreachable_engine_is_not_reported_as_a_missing_session(
    run_app, monkeypatch
):
    """A door is the wrong answer to a backend that is down.

    401 means sign in. A refused connection, a timeout or a 500 means the
    engine, and sending someone to a form they cannot complete would hide the
    one fact they need.
    """
    from conftest import FakeContentClient

    def explode(self):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(FakeContentClient, "whoami", explode, raising=False)
    monkeypatch.setattr(FakeContentClient, "health", explode, raising=False)

    at = run_app("studio")
    assert not at.exception, at.exception
    text = _all_text(at)
    assert "Sign in to Content Studio" not in text
    assert "Back-end unreachable" in text


def test_every_surface_offers_the_others_and_never_itself(run_app):
    """Where else to go, learned from the engine and not from three more
    environment variables per deployment (ADR 0038). Plain anchors, so the
    browser stays in the same tab — moving between rooms of one product."""
    for surface, others in (
        ("studio", ("console", "hometube")),
        ("console", ("studio", "hometube")),
        ("hometube", ("studio", "console")),
    ):
        at = run_app(surface)
        assert not at.exception, (surface, at.exception)
        markup = " ".join(getattr(el, "body", "") for el in at.get("html"))
        for other in others:
            assert f'href="http://{other}.test"' in markup, (surface, other)
        assert f"http://{surface}.test" not in markup, surface
        assert 'target="_blank"' not in markup, surface


def test_a_lonely_surface_offers_nothing(run_app, monkeypatch):
    """An engine that declares no siblings, or only this one: no chips, no
    empty row, no error."""
    from conftest import FakeContentClient

    monkeypatch.setattr(
        FakeContentClient,
        "config",
        lambda self: {
            "credentials": [],
            "surfaces": [
                {"kind": "studio", "title": "Content Studio", "url": "http://s.test"}
            ],
        },
        raising=False,
    )
    at = run_app("studio")
    assert not at.exception, at.exception
    assert not [
        el for el in at.get("html") if "content-elsewhere" in getattr(el, "body", "")
    ]


def test_every_link_a_visitor_follows_uses_the_engines_public_address(
    run_app, monkeypatch
):
    """Not the surface's own setting. The public HomeTube once sent visitors to
    http://api.content.k3s.lab because its CONTENT_PUBLIC_API_URL named the LAN
    while the engine's emails named the public host. Here the surface is set to
    a LAN name on purpose, and nothing it draws may carry it."""
    from conftest import FakeContentClient
    from content_sdk.errors import APIError

    monkeypatch.setenv("CONTENT_PUBLIC_API_URL", "http://api.lan.test")

    def refuse(self):
        raise APIError(401, {"detail": "Authentication required."})

    monkeypatch.setattr(FakeContentClient, "whoami", refuse, raising=False)
    for surface in ("studio", "console", "hometube"):
        at = run_app(surface)
        assert not at.exception, (surface, at.exception)
        buttons = [b.proto.url for b in at.get("link_button")]
        assert buttons and all(
            u.startswith("https://api.public.test/auth/sign-in") for u in buttons
        ), (surface, buttons)
        rendered = " ".join(
            [
                getattr(e, "value", "") or ""
                for k in ("markdown", "caption")
                for e in getattr(at, k)
            ]
        )
        assert "api.lan.test" not in rendered + " ".join(buttons), surface


def test_an_engine_that_declares_no_address_keeps_the_surfaces_own(
    run_app, monkeypatch
):
    """A self-hosted engine has no public address to declare, and nobody
    follows an email there: the surface's setting is the right fallback."""
    from conftest import FakeContentClient
    from content_sdk.errors import APIError

    monkeypatch.setenv("CONTENT_PUBLIC_API_URL", "http://localhost:8010")
    base = FakeContentClient.config

    def no_address(self):
        cfg = dict(base(self))
        cfg.pop("public_api_url", None)
        return cfg

    def refuse(self):
        raise APIError(401, {"detail": "Authentication required."})

    monkeypatch.setattr(FakeContentClient, "config", no_address, raising=False)
    monkeypatch.setattr(FakeContentClient, "whoami", refuse, raising=False)
    at = run_app("studio")
    assert not at.exception, at.exception
    buttons = [b.proto.url for b in at.get("link_button")]
    assert buttons and all(u.startswith("http://localhost:8010/") for u in buttons)


# --- the last request for a source (ADR 0039) ------------------------------------


def _remembered_video_request(folder: str) -> dict:
    """What the engine returns for a source asked for before: the normalized
    request as it was submitted, plus when and that job's state."""
    return {
        "source_ref": "x:item:video",
        "title": "Fake video",
        "requested_at": "2026-09-12T10:00:00+00:00",
        "job": {"job_id": "job_1", "status": "succeeded", "finished_at": None},
        "request": {
            "sources": [
                {
                    "id": "main",
                    "type": "url",
                    "uri": "https://x/video",
                    "auth": None,
                    "provider_args": [],
                }
            ],
            "outputs": [
                {
                    "id": "video_main",
                    "type": "video",
                    "delivery": {"mode": "inherit", "folder": folder, "filename": None},
                    "options": {
                        "selection": {
                            "max_height": 720,
                            "video_codec": {"mode": "prefer", "value": "vp9"},
                            "audio_languages": ["en"],
                        },
                        "container": "mp4",
                        "processing": {
                            "embed_metadata": True,
                            "embed_thumbnail": True,
                            "embed_chapters": False,
                            "embed_subtitles": ["fr"],
                        },
                        "sponsorblock": {
                            "remove": [],
                            "mark": [],
                            "cut_mode": "keyframes",
                        },
                        "cut": None,
                    },
                }
            ],
        },
    }


def test_pasting_a_source_again_finds_last_times_choices(run_app, monkeypatch):
    """Yann, 2026-09-16: the form should come back filled in with what was
    chosen last time — above all the folder, since keeping it is what lets
    Content see what is already there instead of downloading it again."""
    from conftest import FakeContentClient

    monkeypatch.setattr(
        FakeContentClient,
        "_remembered",
        {"x:item:video": _remembered_video_request("Talks")},
    )
    at = run_app("hometube", "https://x/video")
    assert not at.exception, at.exception

    notices = " ".join(el.value for el in at.info)
    assert "You asked for this on 2026-09-12" in notices
    assert "`Talks`" in notices and "succeeded" in notices

    sent = _generation_request(at)
    video = next(o for o in sent["outputs"] if o["type"] == "video")
    assert video["delivery"]["folder"] == "Talks"
    assert video["options"]["selection"]["max_height"] == 720
    assert video["options"]["selection"]["video_codec"]["value"] == "vp9"
    assert video["options"]["selection"]["audio_languages"] == ["en"]
    assert video["options"]["container"] == "mp4"
    assert video["options"]["processing"]["embed_thumbnail"] is True
    assert video["options"]["processing"]["embed_chapters"] is False
    assert video["options"]["processing"]["embed_subtitles"] == ["fr"]
    assert "sponsorblock" not in video["options"]  # "disabled" was the choice


def test_a_folder_that_is_gone_is_proposed_rather_than_dropped(run_app, monkeypatch):
    """The folder of last time is not in the library any more — renamed, or on
    a mount that is not there. Silently landing in the root would hide the
    mistake; proposing it as a new folder shows it."""
    from conftest import FakeContentClient

    monkeypatch.setattr(
        FakeContentClient,
        "_remembered",
        {"x:item:video": _remembered_video_request("Archive/2025")},
    )
    at = run_app("hometube", "https://x/video")
    assert not at.exception, at.exception
    new_folder = at.text_input(key="newfolder-https://x/video")
    assert new_folder.value == "Archive/2025"


def test_the_memory_and_the_french_interface_hold_together(run_app, monkeypatch):
    """The prefill (ADR 0039) and the translated interface were built on two
    branches that both rewrote this form, and merging them was hand work. So
    the two are asserted together: in French, the banner is French *and* last
    time's choices still reach the request."""
    from conftest import FakeContentClient

    monkeypatch.setenv("CONTENT_UI_LANGUAGE", "fr")
    monkeypatch.setattr(
        FakeContentClient,
        "_remembered",
        {"x:item:video": _remembered_video_request("Talks")},
    )
    at = run_app("hometube", "https://x/video")
    assert not at.exception, at.exception

    notices = " ".join(el.value for el in at.info)
    assert "Vous avez demandé ceci le 2026-09-12" in notices
    assert "`Talks`" in notices
    assert "You asked for this" not in notices

    sent = _generation_request(at)
    video = next(o for o in sent["outputs"] if o["type"] == "video")
    assert video["delivery"]["folder"] == "Talks"
    assert video["options"]["selection"]["max_height"] == 720
    assert video["options"]["container"] == "mp4"
    assert video["options"]["processing"]["embed_chapters"] is False


def test_a_source_never_asked_for_gets_the_usual_defaults(run_app):
    at = run_app("hometube", "https://x/video")
    assert not at.exception, at.exception
    assert not [el for el in at.info if "You asked for this" in el.value]
    sent = _generation_request(at)
    video = next(o for o in sent["outputs"] if o["type"] == "video")
    assert "folder" not in (video.get("delivery") or {})
