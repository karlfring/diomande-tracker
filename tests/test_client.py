"""Tests du client : erreurs, quota et pagination, sans réseau."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import client  # noqa: E402


class FausseReponse:
    def __init__(self, payload, restant="90", code=200):
        self._payload, self.status_code = payload, code
        self.headers = {"x-ratelimit-requests-remaining": restant}
        self.text = str(payload)

    def json(self):
        return self._payload


class FausseSession:
    def __init__(self, *reponses):
        self.reponses, self.headers, self.params = list(reponses), {}, []

    def get(self, url, params=None, **k):
        self.params.append(params)
        return self.reponses.pop(0)


@pytest.fixture(autouse=True)
def sans_delai(monkeypatch):
    monkeypatch.setattr(client, "REQUEST_DELAY_S", 0)


def test_cle_absente(monkeypatch):
    monkeypatch.delenv("HIGHLIGHTLY_KEY", raising=False)
    with pytest.raises(client.ApiError):
        client.Highlightly()


def test_cle_refusee():
    api = client.Highlightly(api_key="test", session=FausseSession(
        FausseReponse({"message": "Invalid API key"}, code=401)))
    with pytest.raises(client.ApiError, match="401"):
        api.get("teams", name="Real Madrid")


def test_quota_lu_puis_arret_avant_epuisement():
    api = client.Highlightly(api_key="test", session=FausseSession(FausseReponse({"data": []}, restant="4")))
    api.get("teams")
    assert api.quota_restant == 4
    with pytest.raises(client.QuotaExhausted):
        api.get("teams")


def test_quota_epuise_http_429():
    api = client.Highlightly(api_key="test", session=FausseSession(FausseReponse({}, code=429)))
    with pytest.raises(client.QuotaExhausted):
        api.get("matches", homeTeamId=541)


def test_pagination_parcourt_toutes_les_pages():
    page = lambda ids, total: {"data": [{"id": i} for i in ids],
                               "pagination": {"totalCount": total}, "plan": {"tier": "BASIC"}}
    session = FausseSession(FausseReponse(page([1, 2], 3)), FausseReponse(page([3], 3)))
    api = client.Highlightly(api_key="test", session=session)
    elements, plan = api.get_pagine("matches", homeTeamId=541)
    assert [e["id"] for e in elements] == [1, 2, 3]
    assert [p["offset"] for p in session.params] == [0, 2]
    assert plan["tier"] == "BASIC"
