# Suivi de Yan Diomande

Pipeline qui récupère chaque match de Yan Diomande, archive les données brutes
et alimente un tableau de bord pour suivre son impact et son évolution.

- **Version 0 : saison 2025-2026 au RB Leipzig**, pour construire l'analyse sur
  une saison complète.
- **Ensuite : saison 2026-2027 au Real Madrid**, suivie en continu.

> Saison 2025-2026 ingérée et présentée dans un tableau de bord interactif.
> Étude de cas avec captures : [portfolio de Karl Akpovi](https://karlfring.github.io/portfolio/diomande.html).

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
| Highlightly, offre gratuite | Toutes les ligues, box score avec xG et xA, accès à 2025-26 vérifié | **retenue** |

## Démarrage

```bash
pip install -r requirements.txt

# La clé ne s'écrit jamais dans le code
export HIGHLIGHTLY_KEY=ta_cle            # PowerShell : $env:HIGHLIGHTLY_KEY="ta_cle"
export DIOMANDE_SEASON=2025              # 2025 = RB Leipzig, 2026 = Real Madrid (défaut)

python src/check_access.py               # 4 requêtes : club, calendrier, joueur, un box score
python src/ingest.py                     # récupère les matchs terminés de la saison
python src/export_dashboard.py           # assemble data/dashboard/matchs.json

python src/ingest.py --hors-ligne        # reconstruit les tables depuis l'archive, sans requête

python -m pytest tests/ -q               # 24 tests, sans réseau
```

## Fonctionnement

1. **Identifiants** : le club et le joueur sont retrouvés par leur nom exact
   (pas de confusion avec la réserve du club ou un homonyme), puis mémorisés
   dans `data/raw/<saison>/identifiants.json`.
2. **Calendrier** : deux requêtes récupèrent les matchs à domicile et à
   l'extérieur du club sur la saison, toutes compétitions. Les matchs amicaux restent au calendrier mais sont exclus de l'analyse.
3. **Matchs terminés** : pour chaque match jamais archivé, une requête récupère
   le box score. La réponse brute est archivée dans `data/raw/<saison>/matchs/`,
   avec son empreinte SHA-256 dans `data/raw/<saison>/journal.json`.
4. **Tables** : reconstruites à partir de l'archive dans `data/processed/<saison>/`.
5. **Tableau de bord** : `src/export_dashboard.py` assemble toutes les saisons dans
   `data/dashboard/matchs.json`, une ligne par match officiel.

Un match déjà archivé n'est jamais redemandé : l'exécution est idempotente.
Une saison complète coûte environ 45 requêtes, puis deux ou trois par semaine.
Un garde-fou arrête l'ingestion avant d'épuiser le quota ; les matchs restants
sont repris à l'exécution suivante. Un box score vide juste après un match est
redemandé pendant sept jours, le temps que la source le complète.

## Version 0 : saison 2025-2026 au RB Leipzig

44 matchs au calendrier, dont 6 amicaux exclus. Sur les 38 matchs officiels
(Bundesliga et DFB Pokal), Diomande en a joué 36 : 31 comme titulaire et 5 en
entrant en jeu, pour 2 717 minutes, 13 buts, 9 passes décisives, 7,3 xG et
9,9 xA.

Contrôle de cohérence : en Bundesliga, le pipeline compte 33 matchs joués,
12 buts et 8 passes décisives, comme la fiche du joueur publiée par
[The Analyst](https://theanalyst.com/players/6478/yan-diomande).

## Tableau de bord

Le tableau de bord lit `data/dashboard/matchs.json` et fait tous ses calculs
dans la page. Un filtre de compétition (toutes, Bundesliga, DFB Pokal) met
toute la page à jour.

| Section | Ce qu'elle montre |
|---|---|
| En-tête | la phrase à retenir et cinq chiffres clés : buts, passes décisives, matchs joués, minutes, note moyenne |
| La saison, match par match | une barre par match (temps de jeu, statut), les buts et passes en pastilles, et la fiche du match choisi |
| Buts et passes, comparés aux attentes | buts et passes cumulés face aux xG et xA, avec l'écart |
| Sa note, match après match | la note de chaque match et sa moyenne sur les cinq derniers |
| Ce qu'il produit | ses actions par 90 minutes ou en total, et ses taux de réussite |
| Tous les matchs | le détail des matchs, triable ; un clic sur une ligne ouvre la fiche du match |

Captures et analyse de la saison : [étude de cas](https://karlfring.github.io/portfolio/diomande.html).

## Modèle de données

| Table | Grain | Contenu |
|---|---|---|
| `dim_match` | un match du club | date, club, compétition, adversaire, domicile, score, résultat |
| `dim_competition` | une compétition | identifiant et nom |
| `fact_joueur_match` | un match officiel terminé | statut (titulaire, entré en jeu, sur le banc, n'a pas joué, données indisponibles), minutes, note et statistiques de Diomande |
| `fact_ingestion` | un fichier archivé | fichier, empreinte SHA-256, box score vide ou non, horodatage |

Deux règles de lecture de la source :

- un xG vide quand le joueur n'a pas tiré vaut 0 ;
- une expulsion compte le rouge direct comme le second jaune.

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

## Suite

- **Saison 2026-2027 au Real Madrid** : elle sera ajoutée quand il aura
  disputé assez de matchs pour une analyse solide. Le pipeline et le tableau
  de bord sont déjà prêts à recevoir une seconde saison.
- **Comparaison avec les ailiers des cinq grands championnats** (en
  préparation) : situer ses statistiques par 90 minutes parmi les ailiers à
  900 minutes et plus. Les statistiques de saison de l'API ne donnent ni xG ni
  xA : la comparaison passera par les box scores de tous les matchs (environ
  1 750 sur une saison) et par le profil de chaque joueur, qui donne son poste
  détaillé (ailier gauche, ailier droit).

## Limites connues

- L'offre gratuite précise que certains résultats peuvent être masqués. Le
  workflow de vérification le contrôle sur un vrai box score, et chaque box
  score vide est signalé dans `fact_ingestion`.
- Les tests utilisent des réponses construites selon la structure documentée
  de l'API, avec des valeurs fictives. Les vraies réponses sont archivées dans
  `data/raw/` et servent au contrôle de cohérence ci-dessus.
- Les matchs en sélection nationale ne sont pas encore suivis.
