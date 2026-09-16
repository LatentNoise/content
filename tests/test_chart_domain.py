"""The chart derives every address from one domain.

A deployment used to state the same fact in seven places — each ingress host,
publicApiUrl, each surface's publicUrl, CONTENT_PUBLIC_BASE_URL, the cookie
domain, the default sign-in target and the redirect allowlist. Two of them
disagreed in production, and the public Sign in button pointed at a LAN name
nobody outside the house could reach.

These render the real chart with `helm template` and read what came out, so
they test what a cluster would receive rather than a description of it. They
skip where helm is not installed, and say so.

Standard library only, like the rest of the root suite: no YAML parser, so the
few values read here are found by their line.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
CHART = REPO / "deploy" / "charts" / "content"

pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None, reason="helm is not installed"
)


def _render(*sets: str) -> str:
    args = ["helm", "template", "demo", str(CHART)]
    for value in sets:
        args += ["--set", value]
    done = subprocess.run(args, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip())
    return done.stdout


def _config(rendered: str) -> dict[str, str]:
    block = rendered.split("kind: ConfigMap", 1)[1].split("\n---", 1)[0]
    return dict(re.findall(r'^  (CONTENT_[A-Z_]+): "(.*)"$', block, re.MULTILINE))


def _hosts(rendered: str) -> set[str]:
    return set(re.findall(r'^\s+- host: "([^"]+)"$', rendered, re.MULTILINE))


def _public_api_urls(rendered: str) -> set[str]:
    return set(
        re.findall(r'- name: CONTENT_PUBLIC_API_URL\n\s+value: "([^"]*)"', rendered)
    )


def test_one_domain_names_every_surface():
    rendered = _render("domain=example.org")
    assert _hosts(rendered) == {
        "api.example.org",
        "studio.example.org",
        "console.example.org",
        "hometube.example.org",
    }


def test_one_domain_makes_every_address_agree():
    """The seven facts, all from one."""
    rendered = _render("domain=example.org", "https=true")
    config = _config(rendered)
    assert config["CONTENT_PUBLIC_BASE_URL"] == "https://api.example.org"
    assert _public_api_urls(rendered) == {"https://api.example.org"}
    assert config["CONTENT_SESSION_COOKIE_DOMAIN"] == ".example.org"
    assert config["CONTENT_SESSION_COOKIE_SECURE"] == "true"
    assert config["CONTENT_SIGN_IN_DEFAULT_TARGET"] == "https://studio.example.org"
    assert set(config["CONTENT_ALLOWED_REDIRECT_ORIGINS"].split(",")) == {
        "https://studio.example.org",
        "https://console.example.org",
        "https://hometube.example.org",
    }
    assert set(config["CONTENT_SURFACES"].split(",")) == {
        "studio=https://studio.example.org",
        "console=https://console.example.org",
        "hometube=https://hometube.example.org",
    }


def test_a_lan_without_certificates_gets_a_cookie_it_can_send():
    """A Secure cookie is never sent over http, so `https: false` must make it
    non-Secure — or every sign-in on the LAN completes and carries nothing."""
    config = _config(_render("domain=lab.test", "https=false"))
    assert config["CONTENT_SESSION_COOKIE_SECURE"] == "false"
    assert config["CONTENT_PUBLIC_BASE_URL"] == "http://api.lab.test"


def test_a_subdomain_can_differ_and_everything_follows():
    """The production case: `hometube.<domain>` was already taken by a
    static site, so HomeTube answers on `hometube-app`."""
    rendered = _render("domain=example.org", "uis.hometube.subdomain=hometube-app")
    assert "hometube-app.example.org" in _hosts(rendered)
    config = _config(rendered)
    assert "hometube=https://hometube-app.example.org" in config["CONTENT_SURFACES"]
    origins = config["CONTENT_ALLOWED_REDIRECT_ORIGINS"]
    assert "https://hometube-app.example.org" in origins


def test_an_explicit_value_always_wins():
    rendered = _render(
        "domain=example.org",
        "uis.studio.ingress.host=create.elsewhere.test",
        "config.CONTENT_SIGN_IN_DEFAULT_TARGET=https://landing.example.org",
    )
    assert "create.elsewhere.test" in _hosts(rendered)
    assert _config(rendered)["CONTENT_SIGN_IN_DEFAULT_TARGET"] == (
        "https://landing.example.org"
    )


def test_a_disabled_surface_is_offered_nowhere():
    config = _config(_render("domain=example.org", "uis.console.enabled=false"))
    assert "console" not in config["CONTENT_SURFACES"]
    assert "console" not in config["CONTENT_ALLOWED_REDIRECT_ORIGINS"]


def test_no_domain_and_no_host_is_refused_with_the_way_out():
    """An ingress with no name would answer every hostname on the cluster."""
    with pytest.raises(RuntimeError, match="Set `domain`"):
        _render("domain=")
