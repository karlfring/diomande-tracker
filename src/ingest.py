"""Pipeline d'ingestion : API Highlightly -> CSV en étoile.

Usage :
    python src/ingest.py                 # saison en cours (2026, Real Madrid)
    DIOMANDE_SEASON=2025 python src/ingest.py   # saison passée (RB Leipzig)
    python src/ingest.py --hors-ligne    # reconstruit les tables depuis l'archive

À chaque exécution :
  1. retrouve les identifiants du club et du joueur (une seule fois par
     saison, ensuite lus dans data/raw/<saison>/identifiants.json) ;
  2. récupère le calendrier du club pour la saison (2 requêtes :
     matchs à domicile, matchs à l'extérieur) ;
  3. pour chaque match terminé jamais archivé, récupère le box score
     (1 requête par match) et archive la réponse brute ;
  4. reconstruit les tables à partir de l'archive.

Un match déjà archivé n'est jamais redemandé : l'exécution est idempotente
et ne consomme presque rien les jours sans match.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from client import ApiError, Highlightly, QuotaExhausted  # noqa: E402
from config import (  # noqa: E402
    DATA_OUT,
    DATA_RAW,
    FINISHED_STATES,
    PLAYER_ID,
    PLAYER_NAME,
    SEASON,
    TEAM_ID,
    TEAM_NAME,
)
from parsers import normaliser_nom, parse_box_score, parse_match  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("ingest")

# Un box score vide juste après un match peut être un simple retard de mise
# à jour : on le redemande pendant ce délai, puis on le considère définitif.
DELAI_NOUVEL_ESSAI = timedelta(days=7)
COLONNES_JOURNAL = ["match_id", "fichier", "sha256", "box_score_vide", "ingere_le"]


def _ecrire_json(chemin: Path, payload) -> str:
    """Écrit un JSON stable (clés triées) et renvoie son empreinte SHA-256."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    texte = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1)
    chemin.write_text(texte, encoding="utf-8")
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()


def _lire_json(chemin: Path, defaut=None):
    return json.loads(chemin.read_text(encoding="utf-8")) if chemin.exists() else defaut


def _choisir(candidats: list[dict], nom: str, quoi: str) -> int:
    cible = normaliser_nom(nom)
    exacts = [c for c in candidats
              if cible in (normaliser_nom(c.get("name")), normaliser_nom(c.get("fullName")))]
    if len(exacts) == 1:
        return exacts[0]["id"]
    apercu = ", ".join(f"{c.get('name')} ({c['id']})" for c in candidats[:10]) or "aucun"
    raise ApiError(f"{quoi} « {nom} » : {len(exacts)} correspondance(s) exacte(s). "
                   f"Candidats : {apercu}. Définis l'identifiant à la main.")


def resoudre_identifiants(api: Highlightly, raw_dir: Path) -> dict:
    chemin = raw_dir / "identifiants.json"
    ids = _lire_json(chemin, {}) or {}
    if TEAM_ID:
        ids["team_id"] = TEAM_ID
    if PLAYER_ID:
        ids["player_id"] = PLAYER_ID
    if not ids.get("team_id"):
        equipes, _ = api.get_pagine("teams", name=TEAM_NAME)
        ids["team_id"] = _choisir(equipes, TEAM_NAME, "Club")
        log.info("club %s : identifiant %s", TEAM_NAME, ids["team_id"])
    if not ids.get("player_id"):
        joueurs, _ = api.get_pagine("players", name=PLAYER_NAME)
        ids["player_id"] = _choisir(joueurs, PLAYER_NAME, "Joueur")
        log.info("joueur %s : identifiant %s", PLAYER_NAME, ids["player_id"])
    ids.update(saison=SEASON, club=TEAM_NAME, joueur=PLAYER_NAME)
    _ecrire_json(chemin, ids)
    return ids


def recuperer_calendrier(api: Highlightly, team_id: int, raw_dir: Path) -> list[dict]:
    domicile, plan = api.get_pagine("matches", homeTeamId=team_id, season=SEASON)
    exterieur, _ = api.get_pagine("matches", awayTeamId=team_id, season=SEASON)
    _ecrire_json(raw_dir / f"calendrier_{SEASON}.json",
                 {"domicile": domicile, "exterieur": exterieur, "plan": plan})
    if plan.get("message"):
        log.warning("offre %s : %s", plan.get("tier"), plan["message"])
    vus, matchs = set(), []
    for m in domicile + exterieur:
        if m["id"] not in vus:
            vus.add(m["id"])
            matchs.append(parse_match(m, team_id))
    return matchs


def _box_score_vide(payload) -> bool:
    return not any((e.get("players") for e in (payload or [])))


def ingerer(api: Highlightly, raw_dir: Path = DATA_RAW, out_dir: Path = DATA_OUT,
            maintenant: datetime | None = None) -> dict[str, pd.DataFrame]:
    maintenant = maintenant or datetime.now(timezone.utc)
    ids = resoudre_identifiants(api, raw_dir)
    matchs = recuperer_calendrier(api, ids["team_id"], raw_dir)
    termines = [m for m in matchs if m["statut_match"] in FINISHED_STATES and m["officiel"]]
    log.info("%s matchs au calendrier, %s matchs officiels terminés", len(matchs), len(termines))

    journal = {e["match_id"]: e for e in _lire_json(raw_dir / "journal.json", [])}
    nouveaux = 0
    for m in sorted(termines, key=lambda x: x["date_utc"]):
        chemin = raw_dir / "matchs" / f"{m['match_id']}.json"
        if chemin.exists():
            recent = maintenant - datetime.fromisoformat(m["date_utc"].replace("Z", "+00:00")) < DELAI_NOUVEL_ESSAI
            if not (recent and _box_score_vide(_lire_json(chemin))):
                continue
        try:
            payload = api.get(f"box-score/{m['match_id']}")
        except QuotaExhausted as exc:
            log.warning("%s : arrêt, la suite sera récupérée à la prochaine exécution", exc)
            break
        empreinte = _ecrire_json(chemin, payload)
        journal[m["match_id"]] = {
            "match_id": m["match_id"],
            "fichier": f"matchs/{m['match_id']}.json",
            "sha256": empreinte,
            "box_score_vide": _box_score_vide(payload),
            "ingere_le": maintenant.isoformat(timespec="seconds"),
        }
        nouveaux += 1
        log.info("match %s archivé (%s, %s)", m["match_id"], m["adversaire"], m["date_utc"][:10])
    lignes_journal = sorted(journal.values(), key=lambda e: e["match_id"])
    _ecrire_json(raw_dir / "journal.json", lignes_journal)
    log.info("%s match(s) archivé(s), %s requête(s) utilisée(s), quota restant : %s",
             nouveaux, api.nb_requetes, api.quota_restant)

    tables = construire_tables(matchs, raw_dir, ids["player_id"], lignes_journal)
    out_dir.mkdir(parents=True, exist_ok=True)
    for nom, df in tables.items():
        df.to_csv(out_dir / f"{nom}.csv", index=False, encoding="utf-8")
        log.info("écrit %-22s %4d lignes", f"{nom}.csv", len(df))
    return tables


def construire_tables(matchs: list[dict], raw_dir: Path, player_id: int | None,
                      journal: list[dict]) -> dict[str, pd.DataFrame]:
    dim_match = pd.DataFrame(matchs).sort_values("date_utc").reset_index(drop=True)
    dim_match.insert(1, "club", TEAM_NAME)
    dim_competition = (
        dim_match[["competition_id", "competition"]].drop_duplicates().sort_values("competition_id")
    )
    lignes = []
    for m in matchs:
        chemin = raw_dir / "matchs" / f"{m['match_id']}.json"
        if chemin.exists():
            lignes.append(parse_box_score(_lire_json(chemin), player_id, PLAYER_NAME, m["match_id"]))
    fact = pd.DataFrame(lignes)
    if not fact.empty:
        fact = fact.merge(dim_match[["match_id", "date_utc"]], on="match_id").sort_values("date_utc")
        fact["numero_match"] = range(1, len(fact) + 1)
        fact = fact.drop(columns="date_utc")
    return {
        "dim_match": dim_match,
        "dim_competition": dim_competition,
        "fact_joueur_match": fact,
        "fact_ingestion": pd.DataFrame(journal, columns=COLONNES_JOURNAL),
    }


def reconstruire(raw_dir: Path = DATA_RAW, out_dir: Path = DATA_OUT) -> dict[str, pd.DataFrame]:
    """Reconstruit les tables depuis l'archive, sans aucune requête."""
    ids = _lire_json(raw_dir / "identifiants.json")
    cal = _lire_json(raw_dir / f"calendrier_{SEASON}.json")
    if not ids or not cal:
        raise ApiError(f"Archive incomplète dans {raw_dir} : lance d'abord une ingestion.")
    vus, matchs = set(), []
    for m in cal["domicile"] + cal["exterieur"]:
        if m["id"] not in vus:
            vus.add(m["id"])
            matchs.append(parse_match(m, ids["team_id"]))
    tables = construire_tables(matchs, raw_dir, ids["player_id"], _lire_json(raw_dir / "journal.json", []))
    out_dir.mkdir(parents=True, exist_ok=True)
    for nom, df in tables.items():
        df.to_csv(out_dir / f"{nom}.csv", index=False, encoding="utf-8")
        log.info("écrit %-22s %4d lignes", f"{nom}.csv", len(df))
    return tables


def main() -> int:
    if "--hors-ligne" in sys.argv:
        try:
            reconstruire()
        except ApiError as exc:
            log.error("%s", exc)
            return 1
        return 0
    try:
        api = Highlightly()
        ingerer(api)
    except ApiError as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
