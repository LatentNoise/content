"""The canonical CONTENT_* environment inventory behind the admin console.

Guards the two properties that matter: it never leaks a secret, and it reports
whether each variable was *set* in the environment or fell back to a default.
"""

import dataclasses

from content.config import describe_environment


def test_inventory_shape_and_categories(settings):
    inv = describe_environment(settings, environ={})
    names = {row["name"] for row in inv}
    # A representative variable from each category is present.
    assert {
        "CONTENT_DATA_DIR",
        "CONTENT_MAX_CONCURRENT_JOBS",
        "CONTENT_ALLOW_PRIVATE_NETWORKS",
        "CONTENT_ANTHROPIC_API_KEY",
        "CONTENT_CREDENTIALS",
        "CONTENT_LANGUAGE_PRIMARY",
    } <= names
    for row in inv:
        assert set(row) == {
            "name",
            "category",
            "secret",
            "is_set",
            "value",
            "description",
        }
        assert row["description"]


def test_is_set_reflects_the_environment(settings):
    inv = {r["name"]: r for r in describe_environment(settings, environ={})}
    assert inv["CONTENT_DATA_DIR"]["is_set"] is False

    inv_set = {
        r["name"]: r
        for r in describe_environment(
            settings, environ={"CONTENT_DATA_DIR": "/whatever"}
        )
    }
    # is_set tracks presence in the environment, independent of the value.
    assert inv_set["CONTENT_DATA_DIR"]["is_set"] is True
    assert inv_set["CONTENT_MAX_CONCURRENT_JOBS"]["is_set"] is False


def test_secrets_are_masked_never_exposed(settings):
    configured = dataclasses.replace(
        settings, anthropic_api_key="sk-super-secret", openai_api_key=""
    )
    inv = {r["name"]: r for r in describe_environment(configured, environ={})}

    key = inv["CONTENT_ANTHROPIC_API_KEY"]
    assert key["secret"] is True
    assert "sk-super-secret" not in key["value"]
    assert key["value"].startswith("set")  # presence + length only

    assert inv["CONTENT_OPENAI_API_KEY"]["value"] == "unset"


def test_credentials_show_ids_only(settings):
    from pathlib import Path

    configured = dataclasses.replace(
        settings, credentials={"youtube": Path("/secret/cookies.txt")}
    )
    row = next(
        r
        for r in describe_environment(configured, environ={})
        if r["name"] == "CONTENT_CREDENTIALS"
    )
    assert "youtube" in row["value"]
    assert "cookies.txt" not in row["value"]  # never the path
