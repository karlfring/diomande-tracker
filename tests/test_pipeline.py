"""Tests du pipeline. Les réponses de tests/fixtures suivent la structure
documentée d'API-Football ; les valeurs sont fictives."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import ingest  # noqa: E402
from parsers import parse_fixture, parse_player_match  # noqa: E402

FX = ROOT / "tests" / "fixtures"
PID = 999001


def charger(nom):
    return json.loads((FX / nom).read_text(encoding="utf-8"))


class FauxClient:
    """Remplace l'API : compte les appels et sert les fichiers de test."""

    def __init__(self):
        self.appels = []

    def get(self, endpoint, **params):
        self.appels.append((endpoint, params))
        if endpoint == "fixtures":
            return charger("calendrier.json")
        if endpoint == "fixtures/players":
            return charger(f"match_{params['fixture']}.json")
        raise AssertionError(f"appel inattendu : {endpoint}")


# --- parse_fixture ---------------------------------------------------------
def test_match_a_domicile_et_victoire():
    m = parse_fixture(charger("calendrier.json")["response"][0])
    assert m["domicile"] is True
    assert m["adversaire"] == "Villarreal"
    assert (m["buts_pour"], m["buts_contre"], m["resultat"]) == (1, 0, "V")


def test_match_a_l_exterieur_inverse_le_score():
    m = parse_fixture(charger("calendrier.json")["response"][1])
    assert m["domicile"] is False
    assert m["adversaire"] == "Getafe"
    assert m["resultat"] == "N"


def test_match_a_venir_sans_resultat():
    m = parse_fixture(charger("calendrier.json")["response"][2])
    assert m["resultat"] is None
    assert m["competition"] == "UEFA Champions League"


# --- parse_player_match ----------------------------------------------------
def test_titulaire_statistiques_lues():
    ligne = parse_player_match(charger("match_1001.json"), PID, 1001)
    assert ligne["statut"] == "titulaire"
    assert ligne["minutes"] == 78
    assert ligne["note"] == pytest.approx(7.6)
    assert (ligne["buts"], ligne["tirs"], ligne["dribbles_reussis"]) == (1, 3, 4)


def test_valeurs_textuelles_converties_en_nombres():
    ligne = parse_player_match(charger("match_1001.json"), PID, 1001)
    assert ligne["passes_reussies"] == 19


def test_null_vaut_zero_quand_le_joueur_a_joue():
    ligne = parse_player_match(charger("match_1001.json"), PID, 1001)
    assert ligne["passes_decisives"] == 0
    assert ligne["interceptions"] == 0


def test_sur_le_banc_sans_statistiques():
    ligne = parse_player_match(charger("match_1002.json"), PID, 1002)
    assert ligne["statut"] == "sur le banc"
    assert ligne["minutes"] == 0
    assert ligne["buts"] is None


def test_absent_de_la_feuille_de_match():
    ligne = parse_player_match(charger("match_1001.json"), 424242, 1001)
    assert ligne["statut"] == "absent"
    assert ligne["note"] is None


# --- pipeline --------------------------------------------------------------
def test_seuls_les_matchs_termines_sont_demandes(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "PLAYER_ID", PID)
    api = FauxClient()
    ingest.ingerer(api, raw_dir=tmp_path / "raw", out_dir=tmp_path / "out")
    demandes = sorted(p["fixture"] for e, p in api.appels if e == "fixtures/players")
    assert demandes == [1001, 1002]


def test_deuxieme_execution_idempotente(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "PLAYER_ID", PID)
    ingest.ingerer(FauxClient(), raw_dir=tmp_path / "raw", out_dir=tmp_path / "out")
    api = FauxClient()
    tables = ingest.ingerer(api, raw_dir=tmp_path / "raw", out_dir=tmp_path / "out")
    assert [e for e, _ in api.appels] == ["fixtures"]
    assert len(tables["fact_joueur_match"]) == 2


def test_tables_et_journal(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "PLAYER_ID", PID)
    tables = ingest.ingerer(FauxClient(), raw_dir=tmp_path / "raw", out_dir=tmp_path / "out")
    fact = tables["fact_joueur_match"]
    assert list(fact["match_id"]) == [1002, 1001]          # ordre chronologique
    assert list(fact["numero_match"]) == [1, 2]
    assert len(tables["dim_match"]) == 3
    assert set(tables["fact_ingestion"]["match_id"]) == {1001, 1002}
    assert tables["fact_ingestion"]["sha256"].str.len().eq(64).all()
    assert (tmp_path / "out" / "fact_joueur_match.csv").exists()
