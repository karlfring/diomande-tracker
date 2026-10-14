"""Les tests utilisent des réponses fictives du Real Madrid (saison 2026) :
on fixe le périmètre avant tout import, quelle que soit la saison choisie
dans l'environnement (par exemple dans le workflow d'ingestion 2025)."""

import os

os.environ["DIOMANDE_SEASON"] = "2026"
for nom in ("DIOMANDE_TEAM_ID", "DIOMANDE_PLAYER_ID"):
    os.environ.pop(nom, None)
