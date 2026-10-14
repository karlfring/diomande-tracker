"""Tests du pipeline. Les réponses de tests/fixtures suivent la structure
documentée de l'API Highlightly ; les valeurs sont fictives."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import client  # noqa: E402
import ingest  # noqa: E402
from parsers import lire_score, parse_box_score, parse_match  # noqa: E402

FX = ROOT / "tests" / "fixtures"
PID, RM = 999001, 541
NOM = "Yan Diomande"
MAINTENANT = datetime(2026, 10, 8, tzinfo=timezone.utc)


def charger(nom):
    return json.loads((FX / nom).read_text(encoding="utf-8"))


class FauxClient:
    """Remplace l'API : compte les appels et sert les fichiers de test."""

    get_pagine = client.Highlightly.get_pagine

    def __init__(self, box_scores=None):
        self.appels, self.nb_requetes, self.quota_restant = [], 0, 95
        self.box_scores = box_scores or {}

    def get(self, endpoint, **params):
        self.appels.append(endpoint)
        self.nb_requetes += 1
        if endpoint == "teams":
            return charger("teams.json")
        if endpoint == "players":
            return charger("players.json")
        if endpoint == "matches":
            return charger("matches_home.json" if "homeTeamId" in params else "matches_away.json")
        if endpoint.startswith("box-score/"):
            match_id = int(endpoint.split("/")[1])
            if match_id in self.box_scores:
                return self.box_scores[match_id]
            return charger(f"box_{match_id}.json")
        raise AssertionError(f"appel inattendu : {endpoint}")


def lancer(tmp_path, api=None, maintenant=MAINTENANT):
    api = api or FauxClient()
    tables = ingest.ingerer(api, raw_dir=tmp_path / "raw", out_dir=tmp_path / "out", maintenant=maintenant)
    return api, tables


# --- parse_match -----------------------------------------------------------
def test_lecture_du_score():
    assert lire_score("3 - 1") == (3, 1)
    assert lire_score(None) == (None, None)


def test_match_a_domicile_et_victoire():
    m = parse_match(charger("matches_home.json")["data"][0], RM)
    assert m["domicile"] is True
    assert m["adversaire"] == "Villarreal"
    assert (m["buts_pour"], m["buts_contre"], m["resultat"]) == (1, 0, "V")


def test_match_a_l_exterieur_inverse_le_score():
    m = parse_match(charger("matches_away.json")["data"][0], RM)
    assert m["domicile"] is False
    assert m["adversaire"] == "Real Sociedad"
    assert m["resultat"] == "N"


def test_match_a_venir_sans_resultat():
    m = parse_match(charger("matches_home.json")["data"][2], RM)
    assert m["resultat"] is None
    assert m["competition"] == "UEFA Champions League"


# --- parse_box_score -------------------------------------------------------
def test_titulaire_statistiques_lues():
    ligne = parse_box_score(charger("box_1001.json"), PID, NOM, 1001)
    assert ligne["statut"] == "titulaire"
    assert ligne["minutes"] == 78
    assert ligne["note"] == pytest.approx(7.6)
    assert (ligne["buts"], ligne["tirs"], ligne["dribbles_reussis"]) == (1, 3, 4)
    assert ligne["xg"] == pytest.approx(0.42)


def test_null_vaut_zero_quand_le_joueur_a_joue():
    ligne = parse_box_score(charger("box_1001.json"), PID, NOM, 1001)
    assert ligne["passes_decisives"] == 0
    assert ligne["interceptions"] == 0


def test_entre_en_jeu_et_xa_textuel():
    ligne = parse_box_score(charger("box_1003.json"), PID, NOM, 1003)
    assert ligne["statut"] == "entré en jeu"
    assert ligne["passes_decisives"] == 1
    assert ligne["xa"] == pytest.approx(0.35)
    assert ligne["xg"] is None


def test_sur_le_banc_sans_statistiques():
    ligne = parse_box_score(charger("box_1002.json"), PID, NOM, 1002)
    assert ligne["statut"] == "sur le banc"
    assert ligne["buts"] is None


def test_absent_du_box_score():
    ligne = parse_box_score(charger("box_1001.json"), 424242, "Joueur Inconnu", 1001)
    assert ligne["statut"] == "n'a pas joué"
    assert ligne["note"] is None


def test_box_score_vide_ne_conclut_rien():
    ligne = parse_box_score([], PID, NOM, 1001)
    assert ligne["statut"] == "données indisponibles"


def test_recherche_par_nom_sans_identifiant():
    ligne = parse_box_score(charger("box_1001.json"), None, "yan diomandé", 1001)
    assert ligne["player_id"] == PID
    assert ligne["statut"] == "titulaire"


# --- pipeline --------------------------------------------------------------
def test_identifiants_retrouves_par_nom_exact(tmp_path):
    lancer(tmp_path)
    ids = json.loads((tmp_path / "raw" / "identifiants.json").read_text(encoding="utf-8"))
    assert (ids["team_id"], ids["player_id"]) == (RM, PID)   # pas Castilla, pas Moussa


def test_amical_marque_non_officiel():
    m = parse_match(charger("matches_away.json")["data"][1], RM)
    assert m["officiel"] is False


def test_seuls_les_matchs_officiels_termines_sont_demandes(tmp_path):
    api, _ = lancer(tmp_path)
    assert sorted(a for a in api.appels if a.startswith("box-score")) == [
        "box-score/1001", "box-score/1002", "box-score/1003"]


def test_deuxieme_execution_idempotente(tmp_path):
    lancer(tmp_path)
    api, tables = lancer(tmp_path)
    assert api.appels == ["matches", "matches"]       # identifiants en cache, rien à archiver
    assert len(tables["fact_joueur_match"]) == 3


def test_box_score_vide_redemande_seulement_s_il_est_recent(tmp_path):
    vides = {1001: [], 1003: []}
    lancer(tmp_path, FauxClient(box_scores=vides))
    api, tables = lancer(tmp_path)
    # 1003 (4 octobre) est récent : redemandé ; 1001 (20 septembre) ne l'est plus
    assert [a for a in api.appels if a.startswith("box-score")] == ["box-score/1003"]
    statuts = dict(zip(tables["fact_joueur_match"]["match_id"], tables["fact_joueur_match"]["statut"]))
    assert statuts == {1001: "données indisponibles", 1002: "sur le banc", 1003: "entré en jeu"}


def test_tables_et_journal(tmp_path):
    _, tables = lancer(tmp_path)
    fact = tables["fact_joueur_match"]
    assert list(fact["match_id"]) == [1001, 1002, 1003]       # ordre chronologique
    assert list(fact["numero_match"]) == [1, 2, 3]
    assert len(tables["dim_match"]) == 5
    assert set(tables["fact_ingestion"]["match_id"]) == {1001, 1002, 1003}
    assert tables["fact_ingestion"]["sha256"].str.len().eq(64).all()
    assert (tmp_path / "out" / "fact_joueur_match.csv").exists()
