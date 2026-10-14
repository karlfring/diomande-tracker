"""Vérifie l'accès à API-Football avant de lancer le pipeline.

Usage :
    export APIFOOTBALL_KEY=ta_cle        (Windows PowerShell : $env:APIFOOTBALL_KEY="ta_cle")
    python src/check_access.py

Consomme 3 requêtes. Répond à trois questions :
  1. La clé fonctionne-t-elle, et sur quelle offre ?
  2. La saison en cours est-elle accessible avec cette offre ?
  3. Quel est l'identifiant API-Football de Yan Diomande ?
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from client import ApiError, ApiFootball  # noqa: E402
from config import FINISHED_STATUSES, PLAYER_SEARCH, SEASON, TEAM_ID, TEAM_NAME  # noqa: E402


def dire(message: str) -> None:
    """Affiche le message et, dans GitHub Actions, le publie aussi en annotation
    pour qu'il soit lisible depuis le résumé du run."""
    print(message)
    if os.environ.get("GITHUB_ACTIONS"):
        niveau = "error" if message.startswith(("[ÉCHEC]", "[BLOQUANT]")) else "notice"
        texte = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::{niveau}::{texte}")


def main() -> int:
    try:
        api = ApiFootball()
    except ApiError as exc:
        dire(f"[ÉCHEC] {exc}")
        return 1

    # 1. Compte
    try:
        statut = api.get("status")["response"]
    except ApiError as exc:
        dire(f"[ÉCHEC] Clé refusée : {exc}")
        return 1
    abonnement = statut.get("subscription", {})
    requetes = statut.get("requests", {})
    dire(f"[OK] Clé valide. Offre : {abonnement.get('plan')} "
          f"(active : {abonnement.get('active')}). "
          f"Requêtes aujourd'hui : {requetes.get('current')}/{requetes.get('limit_day')}")

    # 2. Saison en cours
    try:
        matchs = api.get("fixtures", team=TEAM_ID, season=SEASON)["response"]
    except ApiError as exc:
        dire(f"[BLOQUANT] Saison {SEASON}-{SEASON + 1} inaccessible avec cette offre :\n  {exc}")
        dire("  Il faudra passer à l'offre Pro, ou choisir une autre source.")
        return 2
    termines = [m for m in matchs if m["fixture"]["status"]["short"] in FINISHED_STATUSES]
    competitions = sorted({m["league"]["name"] for m in matchs})
    dire(f"[OK] Saison {SEASON}-{SEASON + 1} accessible : {len(matchs)} matchs de {TEAM_NAME} "
          f"au calendrier, {len(termines)} terminés.")
    dire(f"     Compétitions : {', '.join(competitions)}")

    # 3. Identifiant du joueur
    try:
        joueurs = api.get("players", team=TEAM_ID, season=SEASON, search=PLAYER_SEARCH)["response"]
    except ApiError as exc:
        dire(f"[ATTENTION] Recherche du joueur impossible : {exc}")
        return 3
    if not joueurs:
        dire(f"[ATTENTION] Aucun joueur « {PLAYER_SEARCH} » trouvé dans l'effectif {SEASON}.")
        return 3
    for j in joueurs:
        p = j["player"]
        dire(f"[OK] Joueur trouvé : {p['name']} ({p.get('firstname')} {p.get('lastname')}), "
              f"identifiant {p['id']}")
    dire("\nÉtape suivante : ajoute cet identifiant comme variable DIOMANDE_PLAYER_ID "
          "(secret ou variable du dépôt GitHub).")
    dire(f"Quota restant aujourd'hui : {api.quota_restant}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
