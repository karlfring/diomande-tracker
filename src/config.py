"""Configuration du suivi de Yan Diomande."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_RAW_ROOT = ROOT / "data" / "raw"
DATA_OUT_ROOT = ROOT / "data" / "processed"

# --- API-Football (api-sports.io) ----------------------------------------
API_BASE = "https://v3.football.api-sports.io"
# La clé ne doit jamais être écrite dans le code : variable d'environnement
# en local, secret GitHub Actions en production.
API_KEY_ENV = "APIFOOTBALL_KEY"

# Saison au sens API-Football : l'année de début (2025 pour 2025-2026).
# Choisie par la variable d'environnement DIOMANDE_SEASON, 2026 par défaut.
#   2025 : saison passée au RB Leipzig (version 0 du projet)
#   2026 : saison en cours au Real Madrid
SAISONS = {
    2025: {"team_id": 173, "team_name": "RB Leipzig"},
    2026: {"team_id": 541, "team_name": "Real Madrid"},
}
SEASON = int(os.environ.get("DIOMANDE_SEASON") or 2026)
if SEASON not in SAISONS:
    raise ValueError(f"Saison {SEASON} non configurée : ajoute-la dans SAISONS.")
# Toutes les compétitions du club sont suivies (championnat, coupes d'Europe...).
TEAM_ID = SAISONS[SEASON]["team_id"]
TEAM_NAME = SAISONS[SEASON]["team_name"]

# Une archive et un jeu de tables par saison
DATA_RAW = DATA_RAW_ROOT / str(SEASON)
DATA_OUT = DATA_OUT_ROOT / str(SEASON)

# Identifiant API-Football du joueur. Laisser None tant qu'il n'est pas
# connu : `python src/check_access.py` le retrouve et l'affiche.
PLAYER_ID = int(os.environ["DIOMANDE_PLAYER_ID"]) if os.environ.get("DIOMANDE_PLAYER_ID") else None
PLAYER_SEARCH = "Diomande"

# --- Politesse réseau ------------------------------------------------------
# L'offre gratuite limite le nombre de requêtes par minute : on espace les
# appels et on surveille le quota journalier renvoyé dans les en-têtes.
REQUEST_DELAY_S = 6.5
REQUEST_TIMEOUT_S = 30
MAX_RETRIES = 3
MIN_DAILY_QUOTA = 5   # on s'arrête avant d'épuiser le quota du jour

# Statuts API-Football d'un match terminé
FINISHED_STATUSES = {"FT", "AET", "PEN"}
