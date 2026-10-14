"""Tests de la comparaison avec les ailiers du top 5, sans réseau.

Les réponses suivent la structure documentée de l'API Highlightly (ligues,
matchs, box score, profil joueur) ; les joueurs et les valeurs sont fictifs."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import client  # noqa: E402
import comparaison as cmp  # noqa: E402

DIOMANDE = 82718090
MAINTENANT = datetime(2026, 10, 15, tzinfo=timezone.utc)
PLAN = {"tier": "BASIC", "message": "All data available with current plan."}

LIGUES = {
    "England": [{"id": 2001, "name": "Premier League"}, {"id": 2002, "name": "Premier League 2"},
                {"id": 2003, "name": "FA Cup"}],
    "Spain": [{"id": 3001, "name": "LaLiga"}, {"id": 3002, "name": "Segunda División"}],
    "Italy": [{"id": 4001, "name": "Serie A"}, {"id": 4002, "name": "Serie B"}],
    "France": [{"id": 5001, "name": "Ligue 1"}, {"id": 5002, "name": "Ligue 2"}],
}


def match(mid, ligue, jour, etat="Finished", tour="Regular Season - 1"):
    return {"id": mid, "date": f"2025-09-{jour:02d}T15:00:00.000Z", "round": tour,
            "league": {"id": ligue, "season": 2025, "name": "?"},
            "homeTeam": {"id": 1, "name": "A"}, "awayTeam": {"id": 2, "name": "B"},
            "state": {"description": etat, "score": {"current": "1 - 0"}}}


CALENDRIERS = {
    2001: [match(101, 2001, 1), match(102, 2001, 8), match(103, 2001, 15, "Not started")],
    3001: [match(201, 3001, 2)],
    4001: [match(301, 4001, 3)],
    67162: [match(401, 67162, 4), match(402, 67162, 20, tour="Final")],   # barrage : exclu
    5001: [match(501, 5001, 5)],
}


def joueur(pid, nom, poste, minutes, remplacant=False, **stats):
    base = {"goalsScored": 0, "assists": 0, "shotsTotal": 0, "shotsOnTarget": 0, "passesTotal": 20,
            "passesSuccessful": 16, "passesKey": 1, "dribblesTotal": 4, "dribblesSuccessful": 2,
            "duelsTotal": 8, "duelsWon": 4, "fouledByOthers": 1, "expectedGoals": None,
            "expectedAssists": 0.1}
    base.update(stats)
    return {"id": pid, "name": nom, "fullName": nom, "position": poste, "minutesPlayed": minutes,
            "isSubstitute": remplacant, "matchRating": "7.0", "statistics": base}


def box(*joueurs):
    return [{"team": {"id": 1, "name": "Club A"}, "players": list(joueurs)},
            {"team": {"id": 2, "name": "Club B"}, "players": []}]


BOX_SCORES = {
    101: box(joueur(1, "Ailier A", "Forward", 90, goalsScored=1, shotsTotal=3, expectedGoals=0.6),
             joueur(2, "Avant-centre B", "Forward", 90, shotsTotal=2, expectedGoals=0.4),
             joueur(3, "Défenseur C", "Defender", 90)),
    102: box(joueur(1, "Ailier A", "Forward", 90, goalsScored=1, shotsTotal=2, expectedGoals=0.3,
                    expectedAssists=None),
             joueur(2, "Avant-centre B", "Forward", 90, shotsTotal=1, expectedGoals=0.2)),
    201: box(joueur(4, "Ailier D", "Midfielder", 60, remplacant=True)),
    301: box(joueur(5, "Ailier E", "Forward", 90, assists=1, expectedAssists=0.5)),
    501: box(joueur(6, "Gardien F", "Goalkeeper", 90)),
}
# Match du RB Leipzig déjà archivé par le suivi : relu sans requête
BOX_SUIVI = {401: box(joueur(DIOMANDE, "Yan Diomande", "Forward", 90, goalsScored=2, shotsTotal=4,
                             expectedGoals=0.8, dribblesTotal=6, dribblesSuccessful=5))}

PROFILS = {DIOMANDE: "Left Winger", 1: "Right Winger", 2: "Centre-Forward", 5: "Left Winger"}


class FausseApi:
    """Sert les réponses ci-dessus, compte les appels et simule le quota du jour."""

    def __init__(self, quota=95, erreurs=()):
        self.appels, self.nb_requetes, self.quota_restant = [], 0, quota
        self.erreurs = set(erreurs)

    def get(self, endpoint, **params):
        self.appels.append(endpoint)
        self.nb_requetes += 1
        self.quota_restant -= 1
        if endpoint in self.erreurs:
            raise client.ApiError(f"{endpoint} {params} : HTTP 404 introuvable")
        if endpoint == "leagues":
            data = LIGUES[params["countryName"]]
            return {"data": data, "pagination": {"totalCount": len(data)}, "plan": PLAN}
        if endpoint == "matches":
            tout, debut = CALENDRIERS[params["leagueId"]], params.get("offset", 0)
            page = tout[debut:debut + 2]                     # pages de 2 pour tester la pagination
            return {"data": page, "pagination": {"totalCount": len(tout)}, "plan": PLAN}
        if endpoint.startswith("box-score/"):
            return BOX_SCORES[int(endpoint.split("/")[1])]
        if endpoint.startswith("players/"):
            pid = int(endpoint.split("/")[1])
            return [{"id": pid, "name": "?", "profile": {"position": {"main": PROFILS.get(pid, "Defender"),
                                                                       "secondary": None}, "foot": "right"}}]
        raise AssertionError(f"appel inattendu : {endpoint}")


@pytest.fixture
def dossiers(tmp_path, monkeypatch):
    monkeypatch.setattr(cmp, "MINUTES_MIN", 90)
    suivi = tmp_path / "suivi"
    suivi.mkdir()
    for mid, payload in BOX_SUIVI.items():
        (suivi / f"{mid}.json").write_text(json.dumps(payload), encoding="utf-8")
    return {"raw": tmp_path / "raw", "out": tmp_path / "out", "sortie": tmp_path / "comparaison.json",
            "archive_suivi": suivi}


def lancer(api, d):
    return cmp.collecter(api, maintenant=MAINTENANT, diomande=DIOMANDE, **d)


# --- Championnats ---------------------------------------------------------------
def test_championnats_par_nom_exact_sans_homonymes(dossiers):
    api = FausseApi()
    ids = cmp.resoudre_championnats(api, dossiers["raw"])
    assert {k: v["id"] for k, v in ids.items()} == {"ENG": 2001, "ESP": 3001, "ITA": 4001, "GER": 67162, "FRA": 5001}
    assert api.appels.count("leagues") == 4          # Bundesliga déjà connue : aucune requête


def test_championnat_introuvable_bloque(dossiers, monkeypatch):
    monkeypatch.setitem(LIGUES, "Spain", [{"id": 3002, "name": "Segunda División"}])
    with pytest.raises(client.ApiError, match="LaLiga"):
        cmp.resoudre_championnats(FausseApi(), dossiers["raw"])


# --- Collecte ---------------------------------------------------------------------
def test_collecte_complete_et_export(dossiers):
    api = FausseApi()
    etat = lancer(api, dossiers)
    assert etat["complet"] is True
    assert "box-score/401" not in api.appels         # relu dans l'archive du suivi
    assert "box-score/103" not in api.appels         # match pas encore joué
    assert "box-score/402" not in api.appels         # barrage, hors saison régulière
    profils = sorted(a for a in api.appels if a.startswith("players/"))
    assert profils == ["players/1", "players/2", "players/5", f"players/{DIOMANDE}"]
    lignes = json.loads(dossiers["sortie"].read_text(encoding="utf-8"))
    assert [ligne["nom"] for ligne in lignes][0] == "Yan Diomande"
    assert lignes[0]["est_diomande"] is True
    assert {ligne["nom"] for ligne in lignes} == {"Yan Diomande", "Ailier A", "Ailier E"}


def test_actions_par_90_et_percentiles(dossiers):
    lancer(FausseApi(), dossiers)
    lignes = {ligne["nom"]: ligne for ligne in json.loads(dossiers["sortie"].read_text(encoding="utf-8"))}
    a = lignes["Ailier A"]
    assert a["minutes"] == 180 and a["buts"] == 2
    assert a["buts_90"] == pytest.approx(1.0)
    assert a["xg"] == pytest.approx(0.9)
    assert a["poste"] == "Ailier droit"
    d = lignes["Yan Diomande"]
    assert d["buts_90"] == pytest.approx(2.0) and d["pct_buts_90"] == 100
    assert d["taux_dribbles"] == pytest.approx(83.33)
    assert lignes["Ailier E"]["pct_buts_90"] < d["pct_buts_90"]


def test_reserve_de_quota_puis_reprise_sans_doublon(dossiers):
    # 4 ligues + 6 pages de calendrier + profil de Diomande + 3 box scores, puis arrêt
    premiere = FausseApi(quota=cmp.RESERVE_QUOTA + 14)
    etat = lancer(premiere, dossiers)
    assert etat["complet"] is False and "réserve" in etat["arret"]
    assert premiere.quota_restant == cmp.RESERVE_QUOTA
    assert etat["box_scores"]["archives"] == 4       # 3 demandés + 1 relu dans le suivi
    assert not dossiers["sortie"].exists()           # pas d'export tant que la collecte n'est pas finie
    progression = json.loads((dossiers["raw"] / "progression.json").read_text(encoding="utf-8"))
    assert progression["box_scores"] == {"archives": 4, "total": 6, "vides": 0, "erreurs": 0}
    seconde = FausseApi()
    etat = lancer(seconde, dossiers)
    assert etat["complet"] is True
    assert "leagues" not in seconde.appels           # identifiants mémorisés
    deja = set(premiere.appels) - {"leagues", "matches"}
    assert deja and not deja & set(seconde.appels)   # rien n'est redemandé


def test_box_score_introuvable_note_une_seule_fois(dossiers):
    api = FausseApi(erreurs={"box-score/201"})
    etat = lancer(api, dossiers)
    assert etat["complet"] is True and etat["box_scores"]["erreurs"] == 1
    relance = FausseApi(erreurs={"box-score/201"})
    lancer(relance, dossiers)
    assert "box-score/201" not in relance.appels


def test_le_suivi_de_diomande_n_est_pas_modifie(dossiers):
    source = dossiers["archive_suivi"] / "401.json"
    avant = source.read_bytes()
    lancer(FausseApi(), dossiers)
    assert source.read_bytes() == avant
    ecrits = {p.name for p in dossiers["raw"].parent.iterdir()}
    assert ecrits == {"suivi", "raw", "out", "comparaison.json"}


# --- Lecture des box scores et archive -------------------------------------------
def test_xg_vide_sans_tir_vaut_zero_et_xa_manquant_compte():
    lignes = cmp.lignes_box_score(BOX_SCORES[102], 102, "ENG")
    a = next(ligne for ligne in lignes if ligne["nom"] == "Ailier A")
    assert a["xa"] is None
    saison = cmp.agreger(cmp.pd.DataFrame(lignes + cmp.lignes_box_score(BOX_SCORES[201], 201, "ESP")))
    d = saison.set_index("nom").loc["Ailier D"]
    assert d["xg"] == 0.0 and d["minutes"] == 60
    assert saison.set_index("nom").loc["Ailier A", "xa_manquants"] == 1


def test_archive_compressee_stable(tmp_path):
    payload = {"b": 1, "a": [1, 2]}
    e1 = cmp.ecrire_gz(tmp_path / "x.json.gz", payload)
    octets = (tmp_path / "x.json.gz").read_bytes()
    e2 = cmp.ecrire_gz(tmp_path / "x.json.gz", payload)
    assert e1 == e2 and octets == (tmp_path / "x.json.gz").read_bytes()
    assert cmp.lire_gz(tmp_path / "x.json.gz") == payload
