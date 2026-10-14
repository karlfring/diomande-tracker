"""Pipeline d'ingestion : API-Football -> CSV en étoile.

Usage :
    python src/ingest.py

À chaque exécution :
  1. récupère le calendrier du club pour la saison (1 requête) ;
  2. pour chaque match terminé jamais archivé, récupère les statistiques
     des joueurs (1 requête par match) et archive la réponse brute ;
  3. reconstruit les tables à partir de l'archive.

Un match déjà archivé n'est jamais redemandé : l'exécution est idempotente
et ne consomme presque rien les jours sans match.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from client import ApiError, ApiFootball, QuotaExhausted  # noqa: E402
from config import (  # noqa: E402
    DATA_OUT,
    DATA_RAW,
    FINISHED_STATUSES,
    PLAYER_ID,
    PLAYER_SEARCH,
    SEASON,
    TEAM_ID,
)
from parsers import parse_fixture, parse_player_match  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("ingest")


def _ecrire_json(chemin: Path, payload: dict) -> str:
    """Écrit un JSON stable (clés triées) et renvoie son empreinte SHA-256."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    texte = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1)
    chemin.write_text(texte, encoding="utf-8")
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()


def resoudre_joueur(api: ApiFootball) -> int:
    if PLAYER_ID:
        return PLAYER_ID
    res = api.get("players", team=TEAM_ID, season=SEASON, search=PLAYER_SEARCH)["response"]
    if len(res) != 1:
        raise ApiError(f"{len(res)} joueurs trouvés pour « {PLAYER_SEARCH} » : définis DIOMANDE_PLAYER_ID.")
    pid = res[0]["player"]["id"]
    log.warning("DIOMANDE_PLAYER_ID non défini, identifiant résolu : %s", pid)
    return pid


def ingerer(api: ApiFootball, raw_dir: Path = DATA_RAW, out_dir: Path = DATA_OUT) -> dict[str, pd.DataFrame]:
    player_id = resoudre_joueur(api)

    calendrier = api.get("fixtures", team=TEAM_ID, season=SEASON)
    _ecrire_json(raw_dir / f"calendrier_{SEASON}.json", calendrier)
    matchs = [parse_fixture(m) for m in calendrier["response"]]
    termines = [m for m in matchs if m["statut_match"] in FINISHED_STATUSES]
    log.info("%s matchs au calendrier, %s terminés", len(matchs), len(termines))

    journal = _charger_journal(raw_dir)
    nouveaux = 0
    for m in sorted(termines, key=lambda x: x["date_utc"]):
        chemin = raw_dir / "matchs" / f"{m['match_id']}.json"
        if chemin.exists():
            continue
        try:
            payload = api.get("fixtures/players", fixture=m["match_id"])
        except QuotaExhausted as exc:
            log.warning("%s : arrêt, la suite sera récupérée à la prochaine exécution", exc)
            break
        empreinte = _ecrire_json(chemin, payload)
        journal.append({
            "match_id": m["match_id"],
            "fichier": f"matchs/{m['match_id']}.json",
            "sha256": empreinte,
            "ingere_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        nouveaux += 1
        log.info("match %s archivé (%s, %s)", m["match_id"], m["adversaire"], m["date_utc"][:10])
    _ecrire_journal(raw_dir, journal)
    log.info("%s nouveau(x) match(s) archivé(s)", nouveaux)

    tables = construire_tables(matchs, raw_dir, player_id, journal)
    out_dir.mkdir(parents=True, exist_ok=True)
    for nom, df in tables.items():
        df.to_csv(out_dir / f"{nom}.csv", index=False, encoding="utf-8")
        log.info("écrit %-22s %4d lignes", f"{nom}.csv", len(df))
    return tables


def _charger_journal(raw_dir: Path) -> list[dict]:
    chemin = raw_dir / "journal.json"
    return json.loads(chemin.read_text(encoding="utf-8")) if chemin.exists() else []


def _ecrire_journal(raw_dir: Path, journal: list[dict]) -> None:
    (raw_dir / "journal.json").write_text(
        json.dumps(journal, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def construire_tables(matchs: list[dict], raw_dir: Path, player_id: int, journal: list[dict]) -> dict[str, pd.DataFrame]:
    dim_match = pd.DataFrame(matchs).sort_values("date_utc").reset_index(drop=True)
    dim_competition = (
        dim_match[["competition_id", "competition"]].drop_duplicates().sort_values("competition_id")
    )
    lignes = []
    for m in matchs:
        chemin = raw_dir / "matchs" / f"{m['match_id']}.json"
        if chemin.exists():
            payload = json.loads(chemin.read_text(encoding="utf-8"))
            lignes.append(parse_player_match(payload, player_id, m["match_id"]))
    fact = pd.DataFrame(lignes)
    if not fact.empty:
        fact = fact.merge(dim_match[["match_id", "date_utc"]], on="match_id").sort_values("date_utc")
        fact["numero_match"] = range(1, len(fact) + 1)
        fact = fact.drop(columns="date_utc")
    return {
        "dim_match": dim_match,
        "dim_competition": dim_competition,
        "fact_joueur_match": fact,
        "fact_ingestion": pd.DataFrame(journal),
    }


def main() -> int:
    try:
        api = ApiFootball()
        ingerer(api)
    except ApiError as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
