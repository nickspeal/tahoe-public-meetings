"""Workload identity federation: Claude credentials from the Cloud Run service account, no API key."""
from types import SimpleNamespace

from tahoe_ingest import evaluate_topics as et

FEDERATION = {"ANTHROPIC_FEDERATION_RULE_ID": "fdrl_test", "ANTHROPIC_ORGANIZATION_ID": "org-uuid",
              "ANTHROPIC_SERVICE_ACCOUNT_ID": "svac_test", "TAHOE_IDENTITY_TOKEN_SOURCE": "gcp-metadata"}


def clear(monkeypatch):
    for name in [*FEDERATION, "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_IDENTITY_TOKEN",
                 "ANTHROPIC_IDENTITY_TOKEN_FILE", "ANTHROPIC_WORKSPACE_ID", "ANTHROPIC_FEDERATION_AUDIENCE"]:
        monkeypatch.delenv(name, raising=False)


def test_federation_counts_as_credentials(monkeypatch):
    clear(monkeypatch)
    assert not et.has_credentials()
    for name, value in FEDERATION.items():
        monkeypatch.setenv(name, value)
    assert et.uses_gcp_federation() and et.has_credentials()


def test_incomplete_federation_is_not_credentials(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_FEDERATION_RULE_ID", "fdrl_test")
    monkeypatch.setenv("TAHOE_IDENTITY_TOKEN_SOURCE", "gcp-metadata")
    assert not et.has_credentials()


class Response:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *exc): return False
    def read(self): return self.body


def test_metadata_token_request_pins_audience_and_email(monkeypatch):
    clear(monkeypatch)
    seen = []
    def opener(request, timeout):
        seen.append((request, timeout))
        return Response(b"header.payload.sig\n")
    assert et.gcp_identity_token(opener=opener) == "header.payload.sig"
    request, timeout = seen[0]
    assert request.get_header("Metadata-flavor") == "Google" and timeout == 10
    assert "audience=https%3A%2F%2Fapi.anthropic.com" in request.full_url and "format=full" in request.full_url


def test_credentials_use_metadata_provider(monkeypatch):
    clear(monkeypatch)
    for name, value in FEDERATION.items():
        monkeypatch.setenv(name, value)
    captured = {}
    sdk = SimpleNamespace(WorkloadIdentityCredentials=lambda **kwargs: captured.update(kwargs) or "creds")
    assert et.gcp_federated_credentials(sdk) == "creds"
    assert captured["identity_token_provider"] is et.gcp_identity_token
    assert captured["federation_rule_id"] == "fdrl_test" and captured["service_account_id"] == "svac_test"
    assert captured["workspace_id"] is None


def test_real_sdk_accepts_federated_credentials(monkeypatch):
    anthropic = __import__("pytest").importorskip("anthropic")
    clear(monkeypatch)
    for name, value in FEDERATION.items():
        monkeypatch.setenv(name, value)
    client = anthropic.Anthropic(credentials=et.gcp_federated_credentials(anthropic))
    assert client is not None
