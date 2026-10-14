"""Transformation des réponses API-Football en lignes plates."""

from __future__ import annotations

from config import TEAM_ID


def _int(v) -> int | None:
    """API-Football renvoie des nombres, des chaînes ("25") ou null."""
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
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_fixture(m: dict, team_id: int = TEAM_ID) -> dict:
    """Un match de l'endpoint /fixtures, vu depuis l'équipe suivie."""
    home, away = m["teams"]["home"], m["teams"]["away"]
    a_domicile = home["id"] == team_id
    buts_dom, buts_ext = m["goals"]["home"], m["goals"]["away"]
    pour, contre = (buts_dom, buts_ext) if a_domicile else (buts_ext, buts_dom)
    if pour is None or contre is None:
        resultat = None
    else:
        resultat = "V" if pour > contre else ("N" if pour == contre else "D")
    return {
        "match_id": m["fixture"]["id"],
        "date_utc": m["fixture"]["date"],
        "statut_match": m["fixture"]["status"]["short"],
        "competition_id": m["league"]["id"],
        "competition": m["league"]["name"],
        "journee": m["league"].get("round"),
        "domicile": a_domicile,
        "adversaire_id": (away if a_domicile else home)["id"],
        "adversaire": (away if a_domicile else home)["name"],
        "buts_pour": pour,
        "buts_contre": contre,
        "resultat": resultat,
    }


# Statistiques de comptage : null signifie 0 quand le joueur a joué.
_COMPTAGES = {
    "tirs": ("shots", "total"),
    "tirs_cadres": ("shots", "on"),
    "buts": ("goals", "total"),
    "passes_decisives": ("goals", "assists"),
    "passes": ("passes", "total"),
    "passes_cles": ("passes", "key"),
    "passes_reussies": ("passes", "accuracy"),   # nombre de passes réussies sur le match
    "duels": ("duels", "total"),
    "duels_gagnes": ("duels", "won"),
    "dribbles_tentes": ("dribbles", "attempts"),
    "dribbles_reussis": ("dribbles", "success"),
    "fautes_subies": ("fouls", "drawn"),
    "fautes_commises": ("fouls", "committed"),
    "tacles": ("tackles", "total"),
    "interceptions": ("tackles", "interceptions"),
    "cartons_jaunes": ("cards", "yellow"),
    "cartons_rouges": ("cards", "red"),
    "penaltys_obtenus": ("penalty", "won"),
    "penaltys_marques": ("penalty", "scored"),
    "penaltys_rates": ("penalty", "missed"),
}


def parse_player_match(payload: dict, player_id: int, match_id: int) -> dict:
    """Statistiques d'un joueur dans la réponse /fixtures/players d'un match.

    Statut de participation :
      - « titulaire »          : dans le onze de départ
      - « entré en jeu »       : remplaçant ayant joué
      - « sur le banc »        : sur la feuille de match, sans jouer
      - « absent »             : pas sur la feuille de match
    """
    ligne = {"match_id": match_id, "player_id": player_id}
    for equipe in payload.get("response", []):
        for j in equipe.get("players", []):
            if j["player"]["id"] != player_id:
                continue
            s = (j.get("statistics") or [{}])[0]
            jeu = s.get("games", {})
            minutes = _int(jeu.get("minutes")) or 0
            remplacant = bool(jeu.get("substitute"))
            if minutes == 0:
                statut = "sur le banc"
            elif remplacant:
                statut = "entré en jeu"
            else:
                statut = "titulaire"
            ligne.update(
                statut=statut,
                minutes=minutes,
                poste=jeu.get("position"),
                note=_float(jeu.get("rating")),
                capitaine=bool(jeu.get("captain")),
            )
            for col, (bloc, cle) in _COMPTAGES.items():
                v = _int((s.get(bloc) or {}).get(cle))
                ligne[col] = (v or 0) if minutes > 0 else None
            return ligne
    ligne.update(statut="absent", minutes=0, poste=None, note=None, capitaine=False)
    for col in _COMPTAGES:
        ligne[col] = None
    return ligne
