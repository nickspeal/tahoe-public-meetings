import pytest


@pytest.fixture(autouse=True)
def offline_feeds(monkeypatch):
    """The meeting-records step reads YouTube and Laserfiche; tests never reach the network or the Data API."""
    def offline(*args, **kwargs):
        raise OSError("offline test: feed unavailable")
    monkeypatch.setattr("tahoe_ingest.discovery.fetch_text", offline)
    monkeypatch.setattr("tahoe_ingest.records._post_json", offline)
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
