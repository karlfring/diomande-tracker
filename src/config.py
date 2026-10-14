"""Configuration du suivi de Yan Diomande, saison 2026-2027."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = ROOT / "data" / "raw"
DATA_OUT = ROOT / "data" / "processed"

# --- API-Football (api-sports.io) ----------------------------------------
API_BASE = "https://v3.football.api-sports.io"
# La clé ne doit jamais être écrite dans le code : variable d'environnement
# en local, secret GitHub Actions en production.
API_KEY_ENV = "APIFOOTBALL_KEY"

# Saison au sens API-Football : l'année de début (2026 pour 2026-2027).
SEASON = 2026

# Real Madrid dans API-Football. Toutes les compétitions du club sont
# suivies (LaLiga, Ligue des champions, coupes...).
TEAM_ID = 541
TEAM_NAME = "Real Madrid"

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
