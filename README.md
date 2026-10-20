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
| Highlightly, offre gratuite | Toutes les ligues, box score avec xG et xA, accès à 2025-26 et 2026-27 vérifié | **retenue** |

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

python src/comparaison.py                # comparaison : reprend la collecte dans la limite du quota
python src/comparaison.py --hors-ligne   # comparaison : tables et export depuis l'archive

python -m pytest tests/ -q               # 24 tests du suivi et 9 de la comparaison, sans réseau
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

## Commentaire de match par IA générative

`src/commentaire_ia.py` fait rédiger par un modèle de langage (Mistral, via
son API) un court commentaire sur le dernier match de Diomande, puis vérifie
le texte avant de l'archiver.

1. **Des faits, et seulement eux.** Le modèle reçoit les statistiques du
   match (score, minutes, note, buts, xG…) avec une consigne stricte :
   n'utiliser que ces faits et répondre en JSON (titre, commentaire, chiffres
   cités).
2. **Une vérification automatique.** Chaque nombre écrit par le modèle doit
   exister dans les faits fournis, et chaque chiffre cité doit valoir
   exactement la statistique correspondante : « 2 buts » est refusé si le
   joueur en a marqué un. Un modèle peut inventer un chiffre avec assurance ;
   ce contrôle le détecte.
3. **Une archive traçable.** `data/ia/commentaires/<match>.json` garde la
   consigne, les faits envoyés, la réponse, le résultat de la vérification et
   le nombre de tokens consommés.

Seules des statistiques sportives publiques sont envoyées au modèle. Limite
connue : un nombre écrit en toutes lettres (« trois ») n'est pas contrôlé.

```bash
export MISTRAL_API_KEY=ta_cle       # PowerShell : $env:MISTRAL_API_KEY="ta_cle"
python src/commentaire_ia.py                     # dernier match joué
python src/commentaire_ia.py --match 1336360124  # un match précis
```

Le workflow `commentaire-ia.yml` fait la même chose depuis l'onglet Actions,
avec le secret `MISTRAL_API_KEY`.

## Automatisation

Trois workflows GitHub Actions :

- `check-access.yml`, lancé à la main depuis l'onglet Actions, vérifie l'accès
  pour une saison et affiche le résultat dans les annotations du run ;
- `ingest.yml` ingère une saison et commite les nouvelles données. Il tourne
  automatiquement le lundi et le jeudi matin pour la saison en cours au Real
  Madrid, dont l'accès gratuit a été vérifié. Il partage le quota de 100
  requêtes par jour avec la comparaison : les matchs restants sont repris au
  passage suivant ;
- `comparaison.yml` reprend chaque nuit la collecte de la comparaison et
  commite les nouvelles réponses archivées.

À configurer dans le dépôt (Settings > Secrets and variables > Actions) : le
secret `HIGHLIGHTLY_KEY`.

## Suite

- **Saison 2026-2027 au Real Madrid** : elle sera ajoutée quand il aura
  disputé assez de matchs pour une analyse solide. Le pipeline et le tableau
  de bord sont déjà prêts à recevoir une seconde saison.
- **Comparaison avec les ailiers des cinq grands championnats** (collecte en
  cours) : situer ses statistiques par 90 minutes parmi les ailiers à
  900 minutes et plus. Voir la section suivante.

## Comparaison avec les ailiers du top 5 (en cours)

Les statistiques de saison de l'API (`/players/{id}/statistics`) ne donnent ni
xG ni xA, et l'API n'a pas d'endpoint d'effectif. `src/comparaison.py` passe
donc par les matchs, pour la saison 2025-2026 :

1. **Championnats** : Premier League, LaLiga, Serie A, Bundesliga et Ligue 1,
   retrouvés par leur nom exact dans leur pays.
2. **Calendriers** : environ 20 requêtes pour les cinq championnats.
3. **Box scores** : une requête par match terminé de la saison régulière
   (barrages exclus), environ 1 750. Les matchs du RB Leipzig déjà archivés
   par le suivi sont relus, sans requête.
4. **Postes détaillés** : une fois tous les box scores archivés, le profil de
   chaque attaquant et milieu à 900 minutes et plus (environ 800 requêtes)
   donne son poste détaillé. Les ailiers sont les joueurs dont le poste
   principal est « Left Winger » ou « Right Winger ».
5. **Export** : `data/dashboard/comparaison.json`, une ligne par ailier à
   900 minutes et plus : actions par 90 minutes, taux de réussite et
   percentiles parmi les ailiers. Il n'est écrit qu'une fois la collecte complète.

Avec l'offre gratuite, la collecte prend environ un mois : le workflow
`comparaison.yml` la reprend chaque nuit et laisse 15 requêtes du jour au
suivi de Diomande. Son avancement est dans
`data/raw/comparaison/2025/progression.json`. Les réponses brutes sont
archivées en JSON compressé, avec leur empreinte SHA-256, dans
`data/raw/comparaison/2025/`.

Mêmes règles de lecture que le suivi : comptages absents à 0, xG vide sans
tir à 0. Un xA vide compte pour 0 et chaque cas est compté
(`xa_manquants`). La collecte n'écrit jamais dans les dossiers du suivi de
Diomande ni dans `data/dashboard/matchs.json`.

## Limites connues

- L'offre gratuite précise que certains résultats peuvent être masqués. Le
  workflow de vérification le contrôle sur un vrai box score, et chaque box
  score vide est signalé dans `fact_ingestion`.
- Les tests utilisent des réponses construites selon la structure documentée
  de l'API, avec des valeurs fictives. Les vraies réponses sont archivées dans
  `data/raw/` et servent au contrôle de cohérence ci-dessus.
- Les matchs en sélection nationale ne sont pas encore suivis.
