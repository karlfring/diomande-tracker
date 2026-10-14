# Suivi de Yan Diomande

Pipeline qui récupère chaque match de Yan Diomande, archive les données brutes
et alimente un tableau de bord pour suivre son impact et son évolution.

- **Version 0 : saison 2025-2026 au RB Leipzig**, pour construire l'analyse sur
  une saison complète.
- **Ensuite : saison 2026-2027 au Real Madrid**, suivie en continu.

> Projet en construction. Étape actuelle : vérification de l'accès aux données.

## Source de données

[Highlightly Football API](https://highlightly.net/football-api/), offre
gratuite : 100 requêtes par jour, toutes les ligues. Pour chaque match, le
box score donne les statistiques individuelles : minutes, note, buts, passes
décisives, tirs, xG, xA, passes clés, dribbles, duels, fautes subies.

### Pourquoi cette source

| Source envisagée | Constat | Décision |
|---|---|---|
| API-Football, offre gratuite | Vérifié par le workflow d'accès : saisons 2022 à 2024 uniquement, ni 2025-26 ni 2026-27 | écartée tant qu'elle est payante pour ces saisons |
| Understat | Accès automatisé interdit par son fichier robots.txt | écartée |
| Sites protégés contre les robots | Contourner ces protections n'est pas acceptable | écartés |
| Highlightly, offre gratuite | Toutes les ligues, box score avec xG et xA | **retenue**, accès à confirmer par le workflow de vérification |

## Démarrage

```bash
pip install -r requirements.txt

# La clé ne s'écrit jamais dans le code
export HIGHLIGHTLY_KEY=ta_cle            # PowerShell : $env:HIGHLIGHTLY_KEY="ta_cle"
export DIOMANDE_SEASON=2025              # 2025 = RB Leipzig, 2026 = Real Madrid (défaut)

python src/check_access.py               # 4 requêtes : club, calendrier, joueur, un box score
python src/ingest.py                     # récupère les matchs terminés de la saison

python -m pytest tests/ -q               # 21 tests, sans réseau
```

## Fonctionnement

1. **Identifiants** : le club et le joueur sont retrouvés par leur nom exact
   (pas de confusion avec la réserve du club ou un homonyme), puis mémorisés
   dans `data/raw/<saison>/identifiants.json`.
2. **Calendrier** : deux requêtes récupèrent les matchs à domicile et à
   l'extérieur du club sur la saison, toutes compétitions.
3. **Matchs terminés** : pour chaque match jamais archivé, une requête récupère
   le box score. La réponse brute est archivée dans `data/raw/<saison>/matchs/`,
   avec son empreinte SHA-256 dans `data/raw/<saison>/journal.json`.
4. **Tables** : reconstruites à partir de l'archive dans `data/processed/<saison>/`.

Un match déjà archivé n'est jamais redemandé : l'exécution est idempotente.
Une saison complète coûte environ 45 requêtes, puis deux ou trois par semaine.
Un garde-fou arrête l'ingestion avant d'épuiser le quota ; les matchs restants
sont repris à l'exécution suivante. Un box score vide juste après un match est
redemandé pendant sept jours, le temps que la source le complète.

## Modèle de données

| Table | Grain | Contenu |
|---|---|---|
| `dim_match` | un match du club | date, club, compétition, adversaire, domicile, score, résultat |
| `dim_competition` | une compétition | identifiant et nom |
| `fact_joueur_match` | un match terminé | statut (titulaire, entré en jeu, sur le banc, n'a pas joué, données indisponibles), minutes, note et statistiques de Diomande |
| `fact_ingestion` | un fichier archivé | fichier, empreinte SHA-256, box score vide ou non, horodatage |

Le statut « données indisponibles » distingue un box score vide d'un match où
le joueur n'a pas joué : une absence de données n'est jamais comptée comme
une absence du joueur.

## Automatisation

Deux workflows GitHub Actions, lancés depuis l'onglet Actions :

- `check-access.yml` vérifie l'accès pour une saison et affiche le résultat
  dans les annotations du run ;
- `ingest.yml` ingère une saison et commite les nouvelles données. Son
  déclenchement automatique (lundi et jeudi matin) sera activé pour la saison
  en cours une fois l'accès vérifié.

À configurer dans le dépôt (Settings > Secrets and variables > Actions) : le
secret `HIGHLIGHTLY_KEY`.

## Limites connues

- L'offre gratuite précise que certains résultats peuvent être masqués. Le
  workflow de vérification le contrôle sur un vrai box score, et chaque box
  score vide est signalé dans `fact_ingestion`.
- Les tests utilisent des réponses construites selon la structure documentée
  de l'API, avec des valeurs fictives. Ils seront complétés par de vraies
  réponses dès la première ingestion.
- Les matchs en sélection nationale ne sont pas encore suivis.
