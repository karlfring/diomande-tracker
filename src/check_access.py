"""Vérifie l'accès aux données avant de lancer l'ingestion.

Usage :
    export HIGHLIGHTLY_KEY=ta_cle         (PowerShell : $env:HIGHLIGHTLY_KEY="ta_cle")
    export DIOMANDE_SEASON=2025           (facultatif, 2026 par défaut)
    python src/check_access.py

Consomme 4 requêtes : club, calendrier à domicile, joueur, un box score.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from client import ApiError, Highlightly  # noqa: E402
from config import FINISHED_STATES, PLAYER_NAME, SEASON, TEAM_NAME  # noqa: E402
from ingest import _choisir  # noqa: E402
from parsers import parse_box_score  # noqa: E402


def dire(message: str) -> None:
    """Affiche le message et, dans GitHub Actions, le publie aussi en annotation
    pour qu'il soit lisible depuis le résumé du run."""
    print(message)
    if os.environ.get("GITHUB_ACTIONS"):
        niveau = "error" if message.startswith(("[ÉCHEC]", "[BLOQUANT]")) else "notice"
        texte = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::{niveau}::{texte}")


def main() -> int:
    saison = f"{SEASON}-{SEASON + 1}"
    try:
        api = Highlightly()
        page = api.get("teams", name=TEAM_NAME)
    except ApiError as exc:
        dire(f"[ÉCHEC] {exc}")
        return 1
    plan = page.get("plan") or {}
    dire(f"[OK] Clé valide. Offre : {plan.get('tier')}. Message de l'offre : {plan.get('message') or 'aucun'}")
    try:
        team_id = _choisir(page.get("data", []), TEAM_NAME, "Club")
    except ApiError as exc:
        dire(f"[BLOQUANT] {exc}")
        return 2
    dire(f"[OK] Club : {TEAM_NAME}, identifiant {team_id}")

    try:
        page = api.get("matches", homeTeamId=team_id, season=SEASON)
    except ApiError as exc:
        dire(f"[BLOQUANT] Saison {saison} inaccessible : {exc}")
        return 2
    matchs = page.get("data", [])
    termines = [m for m in matchs if (m.get("state") or {}).get("description") in FINISHED_STATES]
    total = (page.get("pagination") or {}).get("totalCount", len(matchs))
    competitions = sorted({(m.get("league") or {}).get("name", "?") for m in matchs})
    dire(f"[OK] Saison {saison} : {total} matchs à domicile au calendrier, {len(termines)} terminés. "
         f"Compétitions : {', '.join(competitions) or 'aucune'}")
    if not termines:
        dire(f"[BLOQUANT] Aucun match terminé pour {TEAM_NAME} en {saison}.")
        return 2

    player_id = None
    try:
        joueurs = api.get("players", name=PLAYER_NAME).get("data", [])
        player_id = _choisir(joueurs, PLAYER_NAME, "Joueur")
        dire(f"[OK] Joueur : {PLAYER_NAME}, identifiant {player_id}")
    except ApiError as exc:
        dire(f"[ATTENTION] {exc} La recherche par nom servira de secours.")

    dernier = max(termines, key=lambda m: m["date"])
    try:
        box = api.get(f"box-score/{dernier['id']}")
    except ApiError as exc:
        dire(f"[BLOQUANT] Box score inaccessible : {exc}")
        return 2
    ligne = parse_box_score(box, player_id, PLAYER_NAME, dernier["id"])
    nb_joueurs = sum(len(e.get("players") or []) for e in box or [])
    adversaire = dernier["awayTeam"]["name"]
    if ligne["statut"] == "données indisponibles":
        dire(f"[BLOQUANT] Box score vide pour {TEAM_NAME} - {adversaire} ({dernier['date'][:10]}) : "
             "l'offre gratuite masque peut-être ces données.")
        return 2
    dire(f"[OK] Box score {TEAM_NAME} - {adversaire} ({dernier['date'][:10]}) : {nb_joueurs} joueurs. "
         f"Diomande : {ligne['statut']}, {ligne['minutes']} min, note {ligne['note']}, "
         f"buts {ligne['buts']}, xG {ligne['xg']}, xA {ligne['xa']}, dribbles réussis {ligne['dribbles_reussis']}")
    dire(f"Requêtes utilisées : {api.nb_requetes}. Quota restant aujourd'hui : {api.quota_restant}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
