"""Tests du client : erreurs métier et garde-fou de quota, sans réseau."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import client  # noqa: E402


class FausseReponse:
    def __init__(self, payload, restant="90", code=200):
        self._payload, self.status_code = payload, code
        self.headers = {"x-ratelimit-requests-remaining": restant}

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class FausseSession:
    def __init__(self, reponse):
        self.reponse, self.headers = reponse, {}

    def get(self, *a, **k):
        return self.reponse


@pytest.fixture(autouse=True)
def sans_delai(monkeypatch):
    monkeypatch.setattr(client, "REQUEST_DELAY_S", 0)


def test_cle_absente(monkeypatch):
    monkeypatch.delenv("APIFOOTBALL_KEY", raising=False)
    with pytest.raises(client.ApiError):
        client.ApiFootball()


def test_erreur_metier_dans_une_reponse_200():
    rep = FausseReponse({"errors": {"plan": "Free plans do not have access to this season"}, "response": []})
    api = client.ApiFootball(api_key="test", session=FausseSession(rep))
    with pytest.raises(client.ApiError, match="Free plans"):
        api.get("fixtures", team=541, season=2026)


def test_quota_lu_puis_arret_avant_epuisement():
    rep = FausseReponse({"errors": [], "response": []}, restant="4")
    api = client.ApiFootball(api_key="test", session=FausseSession(rep))
    api.get("status")
    assert api.quota_restant == 4
    with pytest.raises(client.QuotaExhausted):
        api.get("status")
