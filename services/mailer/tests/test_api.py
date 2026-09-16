"""The HTTP contract: what a product may ask for, and what it may read back."""

from mailer.store import QUEUED


def test_health_is_open_and_reports_the_queue(client):
    client.headers.pop("Authorization")
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_sending_requires_a_credential(client):
    client.headers.pop("Authorization")
    assert client.post("/v1/messages", json={"to": ["a@b.com"]}).status_code == 401


def test_an_unknown_credential_is_refused(client):
    client.headers["Authorization"] = "Bearer not-a-key"
    assert client.post("/v1/messages", json={"to": ["a@b.com"]}).status_code == 401


def test_a_written_message_is_accepted_and_queued(client):
    response = client.post(
        "/v1/messages",
        json={"to": ["someone@example.com"], "subject": "Hello", "text": "Body"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == QUEUED
    assert body["to"] == ["someone@example.com"]
    assert "text" not in body  # bodies never come back out


def test_a_template_supplies_subject_and_bodies(client, store):
    response = client.post(
        "/v1/messages",
        json={
            "to": ["someone@example.com"],
            "template": "magic-link",
            "variables": {"link": "https://x.test/t", "product": "Content", "minutes": "15"},
        },
    )
    assert response.status_code == 202
    message = store.get(response.json()["id"])
    assert message.subject.startswith("Your Content sign-in link · ")
    assert "https://x.test/t" in message.text_body
    assert message.html_body and "https://x.test/t" in message.html_body


def test_a_template_missing_a_variable_is_rejected(client):
    response = client.post(
        "/v1/messages",
        json={"to": ["a@b.com"], "template": "magic-link", "variables": {"link": "https://x"}},
    )
    assert response.status_code == 422


def test_an_unknown_template_is_rejected(client):
    response = client.post(
        "/v1/messages", json={"to": ["a@b.com"], "template": "nope", "variables": {}}
    )
    assert response.status_code == 422


def test_a_body_and_a_template_together_are_rejected(client):
    response = client.post(
        "/v1/messages",
        json={"to": ["a@b.com"], "template": "magic-link", "text": "x"},
    )
    assert response.status_code == 422


def test_a_body_without_a_subject_is_rejected(client):
    assert client.post("/v1/messages", json={"to": ["a@b.com"], "text": "x"}).status_code == 422


def test_the_default_sender_is_used_unless_overridden(client, store):
    default = client.post(
        "/v1/messages", json={"to": ["a@b.com"], "subject": "s", "text": "t"}
    ).json()
    assert store.get(default["id"]).from_addr == "Test <test@latentnoise.dev>"
    chosen = client.post(
        "/v1/messages",
        json={"to": ["a@b.com"], "subject": "s", "text": "t", "from": "Other <o@latentnoise.dev>"},
    ).json()
    assert store.get(chosen["id"]).from_addr == "Other <o@latentnoise.dev>"


def test_a_client_cannot_read_another_clients_message(client):
    mine = client.post("/v1/messages", json={"to": ["a@b.com"], "subject": "s", "text": "t"}).json()
    assert client.get(f"/v1/messages/{mine['id']}").status_code == 200
    client.headers["Authorization"] = "Bearer other-key"
    assert client.get(f"/v1/messages/{mine['id']}").status_code == 404


def test_config_never_reveals_the_provider(client, api_key):
    body = client.get("/v1/config").json()
    assert body["clients"] == ["content", "studio"]
    assert "smtp_host" not in body
    assert "smtp_password" not in body
    assert api_key not in str(body)
