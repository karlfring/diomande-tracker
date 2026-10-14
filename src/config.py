"""Configuration du suivi de Yan Diomande."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_RAW_ROOT = ROOT / "data" / "raw"
DATA_OUT_ROOT = ROOT / "data" / "processed"

# --- Source : Highlightly Football API ------------------------------------
# Offre gratuite : 100 requêtes par jour, toutes les ligues, statistiques
# individuelles par match (box score) avec xG et xA.
API_BASE = "https://soccer.highlightly.net"
# La clé ne doit jamais être écrite dans le code : variable d'environnement
# en local, secret GitHub Actions en production.
API_KEY_ENV = "HIGHLIGHTLY_KEY"

# --- Périmètre --------------------------------------------------------------
# Saison = année de début (2025 pour 2025-2026), choisie par la variable
# d'environnement DIOMANDE_SEASON.
#   2025 : saison passée au RB Leipzig (version 0 du projet)
#   2026 : saison en cours au Real Madrid
SAISONS = {
    2025: "RB Leipzig",
    2026: "Real Madrid",
}
SEASON = int(os.environ.get("DIOMANDE_SEASON") or 2026)
if SEASON not in SAISONS:
    raise ValueError(f"Saison {SEASON} non configurée : ajoute-la dans SAISONS.")
TEAM_NAME = SAISONS[SEASON]

PLAYER_NAME = "Yan Diomande"


def _id_env(nom: str) -> int | None:
    v = os.environ.get(nom)
    return int(v) if v else None


# Identifiants Highlightly. Facultatifs : sinon ils sont retrouvés par nom
# au premier passage puis mémorisés dans data/raw/<saison>/identifiants.json.
TEAM_ID = _id_env("DIOMANDE_TEAM_ID")
PLAYER_ID = _id_env("DIOMANDE_PLAYER_ID")

# Une archive et un jeu de tables par saison
DATA_RAW = DATA_RAW_ROOT / str(SEASON)
DATA_OUT = DATA_OUT_ROOT / str(SEASON)

# --- Politesse réseau -------------------------------------------------------
REQUEST_DELAY_S = 1.0
REQUEST_TIMEOUT_S = 30
MAX_RETRIES = 3
MIN_DAILY_QUOTA = 5   # on s'arrête avant d'épuiser le quota du jour

# Compétitions ignorées pour l'analyse : les matchs amicaux restent dans le
# calendrier (colonne `officiel` à False) mais leur box score n'est pas demandé.
MOTS_AMICAUX = ("friendl", "amical")

# États Highlightly d'un match terminé
FINISHED_STATES = {"Finished", "Finished after penalties", "Finished after extra time"}
