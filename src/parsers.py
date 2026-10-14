"""Transformation des réponses Highlightly en lignes plates."""

from __future__ import annotations

import re
import unicodedata

from config import MOTS_AMICAUX


def _int(v) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def normaliser_nom(nom: str | None) -> str:
    """« Yan Diomandé » et « yan diomande » donnent la même clé."""
    if not nom:
        return ""
    sans_accents = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sans_accents).strip().lower()


def lire_score(score: str | None) -> tuple[int | None, int | None]:
    """« 3 - 1 » donne (3, 1) ; un score absent donne (None, None)."""
    m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", score or "")
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def parse_match(m: dict, team_id: int) -> dict:
    """Un match de l'endpoint /matches, vu depuis l'équipe suivie."""
    home, away = m["homeTeam"], m["awayTeam"]
    a_domicile = home["id"] == team_id
    etat = m.get("state") or {}
    buts_dom, buts_ext = lire_score((etat.get("score") or {}).get("current"))
    pour, contre = (buts_dom, buts_ext) if a_domicile else (buts_ext, buts_dom)
    if pour is None or contre is None:
        resultat = None
    else:
        resultat = "V" if pour > contre else ("N" if pour == contre else "D")
    adversaire = away if a_domicile else home
    ligue = m.get("league") or {}
    nom_competition = ligue.get("name") or ""
    return {
        "match_id": m["id"],
        "date_utc": m["date"],
        "statut_match": etat.get("description"),
        "saison": ligue.get("season"),
        "competition_id": ligue.get("id"),
        "competition": nom_competition,
        "officiel": not any(mot in nom_competition.lower() for mot in MOTS_AMICAUX),
        "journee": m.get("round"),
        "domicile": a_domicile,
        "adversaire_id": adversaire["id"],
        "adversaire": adversaire["name"],
        "buts_pour": pour,
        "buts_contre": contre,
        "resultat": resultat,
    }


# Statistiques du box score. Les comptages absents valent 0 quand le
# joueur a joué ; xG et xA restent vides s'ils ne sont pas fournis.
_COMPTAGES = {
    "buts": "goalsScored",
    "passes_decisives": "assists",
    "tirs": "shotsTotal",
    "tirs_cadres": "shotsOnTarget",
    "passes": "passesTotal",
    "passes_reussies": "passesSuccessful",
    "passes_cles": "passesKey",
    "dribbles_tentes": "dribblesTotal",
    "dribbles_reussis": "dribblesSuccessful",
    "duels": "duelsTotal",
    "duels_gagnes": "duelsWon",
    "fautes_subies": "fouledByOthers",
    "fautes_commises": "fouledOthers",
    "tacles": "tacklesTotal",
    "interceptions": "interceptionsTotal",
    "cartons_jaunes": "cardsYellow",
    "cartons_rouges": "cardsRed",
    "penaltys_marques": "penaltiesScored",
    "penaltys_rates": "penaltiesMissed",
}
_DECIMALES = {"xg": "expectedGoals", "xa": "expectedAssists"}
COLONNES_STATS = ["hors_jeu", *_COMPTAGES, *_DECIMALES]


def _trouver_joueur(payload, player_id: int | None, player_name: str):
    cible = normaliser_nom(player_name)
    par_nom = None
    for equipe in payload or []:
        for j in equipe.get("players") or []:
            if player_id is not None and j.get("id") == player_id:
                return equipe, j
            if par_nom is None and cible and cible in (
                normaliser_nom(j.get("name")), normaliser_nom(j.get("fullName"))
            ):
                par_nom = (equipe, j)
    return par_nom or (None, None)


def parse_box_score(payload, player_id: int | None, player_name: str, match_id: int) -> dict:
    """Statistiques du joueur dans la réponse /box-score/{matchId}.

    Statut de participation :
      - « titulaire »              : dans le onze de départ
      - « entré en jeu »           : remplaçant ayant joué
      - « sur le banc »            : listé sans avoir joué
      - « n'a pas joué »           : absent du box score (banc, blessure, choix)
      - « données indisponibles »  : box score vide, rien ne permet de conclure
    """
    ligne = {"match_id": match_id, "player_id": player_id}
    vide = not any((e.get("players") for e in (payload or [])))
    _, j = _trouver_joueur(payload, player_id, player_name)
    if j is None:
        ligne.update(statut="données indisponibles" if vide else "n'a pas joué",
                     minutes=0, poste=None, note=None, capitaine=False)
        for col in COLONNES_STATS:
            ligne[col] = None
        return ligne

    minutes = _int(j.get("minutesPlayed")) or 0
    if minutes == 0:
        statut = "sur le banc"
    elif j.get("isSubstitute"):
        statut = "entré en jeu"
    else:
        statut = "titulaire"
    ligne.update(
        player_id=j.get("id", player_id),
        statut=statut,
        minutes=minutes,
        poste=j.get("position"),
        note=_float(j.get("matchRating")),
        capitaine=bool(j.get("isCaptain")),
    )
    s = j.get("statistics") or {}
    a_joue = minutes > 0
    ligne["hors_jeu"] = (_int(j.get("offsides")) or 0) if a_joue else None
    for col, cle in _COMPTAGES.items():
        ligne[col] = (_int(s.get(cle)) or 0) if a_joue else None
    for col, cle in _DECIMALES.items():
        ligne[col] = _float(s.get(cle)) if a_joue else None
    return ligne
