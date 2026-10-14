# Suivi de Yan Diomande, saison 2026-2027

Pipeline qui récupère automatiquement chaque match de Yan Diomande avec le
Real Madrid (toutes compétitions), archive les données et alimente un tableau
de bord pour suivre son impact et son évolution au fil de la saison.

> Projet en construction. Étape actuelle : vérification de l'accès aux données.

## Source de données

[API-Football](https://www.api-football.com/) (api-sports.io), API officielle
avec clé. Pour chaque match : minutes, note, tirs, buts, passes décisives,
passes clés, dribbles, duels, fautes subies.

## Démarrage

```bash
pip install -r requirements.txt

# La clé ne s'écrit jamais dans le code
export APIFOOTBALL_KEY=ta_cle            # PowerShell : $env:APIFOOTBALL_KEY="ta_cle"

python src/check_access.py               # 3 requêtes : offre, saison, identifiant du joueur
export DIOMANDE_PLAYER_ID=identifiant    # valeur affichée par check_access.py
python src/ingest.py                     # récupère les matchs terminés

python -m pytest tests/ -q               # 14 tests, sans réseau
```

## Fonctionnement

1. **Calendrier** : une requête récupère tous les matchs du club pour la saison.
2. **Matchs terminés** : pour chaque match jamais archivé, une requête récupère
   les statistiques des joueurs. La réponse brute est archivée dans
   `data/raw/matchs/`, avec son empreinte SHA-256 dans `data/raw/journal.json`.
3. **Tables** : reconstruites à partir de l'archive dans `data/processed/`.

Un match déjà archivé n'est jamais redemandé : l'exécution est idempotente et
consomme deux ou trois requêtes par semaine, bien en dessous des 100 requêtes
par jour de l'offre gratuite. Un garde-fou arrête l'ingestion avant
d'épuiser le quota ; les matchs restants sont repris à l'exécution suivante.

## Modèle de données

| Table | Grain | Contenu |
|---|---|---|
| `dim_match` | un match du club | date, compétition, adversaire, domicile, score, résultat |
| `dim_competition` | une compétition | identifiant et nom |
| `fact_joueur_match` | un match terminé | statut (titulaire, entré en jeu, sur le banc, absent), minutes, note et statistiques de Diomande |
| `fact_ingestion` | un fichier archivé | fichier, empreinte SHA-256, horodatage |

## Automatisation

`.github/workflows/ingest.yml` s'exécute le lundi et le jeudi matin : tests,
ingestion, puis commit des nouvelles données. À configurer dans le dépôt
GitHub (Settings > Secrets and variables > Actions) :

- secret `APIFOOTBALL_KEY` : la clé API ;
- variable `DIOMANDE_PLAYER_ID` : l'identifiant affiché par `check_access.py`.

## Limites connues

- Les tests utilisent des réponses construites selon la structure documentée
  de l'API, avec des valeurs fictives. Ils seront complétés par de vraies
  réponses dès le premier appel réussi.
- L'offre gratuite d'API-Football est limitée en saisons : `check_access.py`
  indique si la saison en cours est accessible.
