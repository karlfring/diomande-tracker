"""Comparaison avec les ailiers des cinq grands championnats européens.

Usage :
    python src/comparaison.py               # collecte, dans la limite du quota du jour
    python src/comparaison.py --hors-ligne  # tables et export depuis l'archive, sans requête

Les statistiques de saison de l'API (/players/{id}/statistics) ne donnent ni
xG ni xA, et l'API n'a pas d'endpoint d'effectif. La comparaison passe donc par
les box scores de tous les matchs de championnat de la saison, puis par le
profil des joueurs, qui donne leur poste détaillé (« Left Winger »...).

La collecte se fait en plusieurs exécutions (offre gratuite : 100 requêtes par
jour) et reprend toujours où elle s'est arrêtée :
  1. identifiants des cinq championnats (une fois) ;
  2. calendrier de chaque championnat (une fois, environ 20 requêtes) ;
  3. box score de chaque match terminé, une requête par match. Ceux du
     RB Leipzig déjà archivés par le suivi de Diomande sont relus, sans requête ;
  4. une fois tous les box scores archivés, profil des attaquants et des
     milieux à 900 minutes et plus, pour leur poste détaillé.

Chaque réponse brute est archivée (gzip) avec son empreinte SHA-256 et n'est
jamais redemandée. Une réserve de requêtes reste chaque jour au suivi de
Diomande. Ce module n'écrit que dans data/raw/comparaison/,
data/processed/comparaison/ et data/dashboard/comparaison.json : le suivi de
Diomande, ses tables et le fichier du tableau de bord actuel ne changent pas.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from client import ApiError, Highlightly, QuotaExhausted  # noqa: E402
from config import DATA_RAW_ROOT, FINISHED_STATES, ROOT  # noqa: E402
from parsers import _COMPTAGES, _DECIMALES, _float, _int, normaliser_nom  # noqa: E402

log = logging.getLogger("comparaison")

# --- Périmètre --------------------------------------------------------------
SAISON = int(os.environ.get("COMPARAISON_SEASON") or 2025)
RAW = ROOT / "data" / "raw" / "comparaison" / str(SAISON)
OUT = ROOT / "data" / "processed" / "comparaison" / str(SAISON)
SORTIE = ROOT / "data" / "dashboard" / "comparaison.json"
# Box scores déjà archivés par le suivi de Diomande : relus, jamais modifiés
ARCHIVE_SUIVI = DATA_RAW_ROOT / str(SAISON) / "matchs"
IDENTIFIANTS_SUIVI = DATA_RAW_ROOT / str(SAISON) / "identifiants.json"

CHAMPIONNATS = [
    {"code": "ENG", "nom": "Premier League", "pays": "England", "alias": ["Premier League"]},
    {"code": "ESP", "nom": "LaLiga", "pays": "Spain",
     "alias": ["La Liga", "LaLiga", "LaLiga EA Sports", "Primera Division", "Primera División"]},
    {"code": "ITA", "nom": "Serie A", "pays": "Italy", "alias": ["Serie A"]},
    # Identifiant déjà connu par le suivi de Diomande (dim_competition)
    {"code": "GER", "nom": "Bundesliga", "pays": "Germany", "alias": ["Bundesliga"], "id": 67162},
    {"code": "FRA", "nom": "Ligue 1", "pays": "France",
     "alias": ["Ligue 1", "Ligue 1 McDonald's", "Ligue 1 Uber Eats"]},
]

RESERVE_QUOTA = 15        # requêtes laissées chaque jour au suivi de Diomande
MINUTES_MIN = 900         # seuil pour entrer dans la comparaison
POSTES_CANDIDATS = {"Forward", "Midfielder"}     # poste noté dans les box scores
POSTES_AILIERS = {"Left Winger": "Ailier gauche", "Right Winger": "Ailier droit"}
ETATS_FINAUX = FINISHED_STATES | {"Cancelled", "Postponed", "Abandoned", "Awarded"}
# Saison régulière uniquement : les barrages (« Final », « Semi-finals »...)
# opposent aussi des clubs de deuxième division.
SAISON_REGULIERE = "Regular Season"

# Statistiques additionnées sur la saison (colonnes des box scores)
STATS = list(_COMPTAGES) + list(_DECIMALES)
# Actions ramenées à 90 minutes, puis classées en percentiles parmi les ailiers
PAR_90 = ["buts", "passes_decisives", "xg", "xa", "xg_xa", "tirs", "tirs_cadres",
          "passes_cles", "dribbles_reussis", "duels_gagnes", "fautes_subies"]
TAUX = {"taux_dribbles": ("dribbles_reussis", "dribbles_tentes"),
        "taux_passes": ("passes_reussies", "passes"),
        "taux_duels": ("duels_gagnes", "duels")}


class Arret(Exception):
    """Réserve de quota atteinte : la suite sera collectée à la prochaine exécution."""


def dire(message: str, niveau: str = "notice", annotation: bool = True) -> None:
    """Affiche le message et, si demandé, le publie en annotation dans GitHub
    Actions (10 annotations au plus par étape : on les réserve à l'essentiel)."""
    print(message, flush=True)
    if annotation and os.environ.get("GITHUB_ACTIONS"):
        texte = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::{niveau}::{texte}", flush=True)


# --- Archive ----------------------------------------------------------------
def _texte(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8")


def ecrire_gz(chemin: Path, payload) -> str:
    """Écrit le JSON compressé (octets stables) et renvoie l'empreinte SHA-256 du JSON."""
    donnees = _texte(payload)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with open(chemin, "wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=0, filename="") as gz:
        gz.write(donnees)
    return hashlib.sha256(donnees).hexdigest()


def lire_gz(chemin: Path):
    return json.loads(gzip.decompress(chemin.read_bytes()).decode("utf-8"))


def ecrire_json(chemin: Path, payload) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_bytes(_texte(payload))


def lire_json(chemin: Path, defaut=None):
    return json.loads(chemin.read_text(encoding="utf-8")) if chemin.exists() else defaut


# --- Appels à l'API, avec réserve -------------------------------------------
def appeler(api, endpoint: str, **params):
    if api.quota_restant is not None and api.quota_restant <= RESERVE_QUOTA:
        raise Arret(f"réserve de {RESERVE_QUOTA} requêtes atteinte ({api.quota_restant} restantes)")
    try:
        return api.get(endpoint, **params)
    except QuotaExhausted as exc:
        raise Arret(str(exc)) from exc


def traiter_erreur(exc: ApiError, cle: str, journal: dict, maintenant: datetime) -> None:
    """Ressource introuvable (404, 400) : notée une fois pour toutes. Clé refusée
    (401, 403) : arrêt immédiat. Autre panne (réseau, serveur) : la suite est
    reportée à la prochaine exécution, sans rien noter."""
    texte = str(exc)
    if "HTTP 401" in texte or "HTTP 403" in texte:
        raise exc
    if "HTTP 404" in texte or "HTTP 400" in texte:
        _noter(journal, cle, None, None, maintenant, erreur=texte[:300])
        log.warning("%s : %s", cle, texte[:200])
        return
    raise Arret(f"panne sur {cle} : {texte[:200]}") from exc


def pages(api, endpoint: str, **params) -> tuple[list, dict]:
    elements, offset, plan = [], 0, {}
    while True:
        page = appeler(api, endpoint, offset=offset, **params)
        donnees = page.get("data", [])
        plan = page.get("plan") or plan
        elements.extend(donnees)
        total = (page.get("pagination") or {}).get("totalCount", len(elements))
        offset += len(donnees)
        if not donnees or offset >= total:
            return elements, plan


# --- 1. Championnats -----------------------------------------------------------
def resoudre_championnats(api, raw: Path = RAW) -> dict:
    chemin = raw / "championnats.json"
    connus = lire_json(chemin, {}) or {}
    for c in CHAMPIONNATS:
        if c["code"] in connus:
            continue
        if c.get("id"):
            connus[c["code"]] = {"id": c["id"], "nom": c["nom"], "pays": c["pays"]}
            continue
        ligues, plan = pages(api, "leagues", countryName=c["pays"], season=SAISON)
        cibles = {normaliser_nom(a) for a in c["alias"]}
        exacts = [lg for lg in ligues if normaliser_nom(lg.get("name")) in cibles]
        if len(exacts) != 1:
            noms = ", ".join(sorted({f"{lg.get('name')} ({lg.get('id')})" for lg in ligues})) or "aucune"
            raise ApiError(f"{c['nom']} : {len(exacts)} correspondance(s) parmi les ligues de {c['pays']} : "
                           f"{noms}. Ajoute son identifiant dans CHAMPIONNATS.")
        connus[c["code"]] = {"id": exacts[0]["id"], "nom": exacts[0]["name"], "pays": c["pays"]}
        dire(f"[OK] {c['nom']} : identifiant {exacts[0]['id']} (offre {plan.get('tier')} : {plan.get('message')})",
             annotation=False)
        ecrire_json(chemin, connus)
    ecrire_json(chemin, connus)
    return connus


# --- 2. Calendriers ----------------------------------------------------------
def recuperer_calendriers(api, championnats: dict, raw: Path = RAW) -> list[dict]:
    """Matchs terminés des cinq championnats. Un calendrier n'est redemandé
    que s'il contient encore des matchs à jouer (saison en cours)."""
    matchs = []
    for code, info in championnats.items():
        chemin = raw / "calendriers" / f"{code}.json"
        archive = lire_json(chemin)
        a_jour = archive is not None and all(
            (m.get("state") or {}).get("description") in ETATS_FINAUX for m in archive["matchs"])
        if not a_jour:
            liste, plan = pages(api, "matches", leagueId=info["id"], season=SAISON)
            archive = {"championnat": code, "league_id": info["id"], "saison": SAISON,
                       "plan": plan, "matchs": liste}
            ecrire_json(chemin, archive)
            termines = sum((m.get("state") or {}).get("description") in FINISHED_STATES for m in liste)
            dire(f"[OK] {info['nom']} {SAISON}-{SAISON + 1} : {len(liste)} matchs au calendrier, {termines} terminés",
                 annotation=False)
        matchs += matchs_retenus(archive["matchs"], code)
    vus, uniques = set(), []
    for m in sorted(matchs, key=lambda x: (x["date"], x["match_id"])):
        if m["match_id"] not in vus:
            vus.add(m["match_id"])
            uniques.append(m)
    return uniques


def matchs_retenus(liste: list[dict], code: str) -> list[dict]:
    """Matchs terminés de la saison régulière d'un championnat."""
    return [{"match_id": m["id"], "date": m["date"], "championnat": code} for m in liste
            if (m.get("state") or {}).get("description") in FINISHED_STATES
            and str(m.get("round") or "").startswith(SAISON_REGULIERE)]


# --- 3. Box scores ---------------------------------------------------------------
def _vide(payload) -> bool:
    return not any(e.get("players") for e in (payload or []))


def _noter(journal: dict, cle: str, fichier: str, empreinte: str | None, maintenant: datetime, **extra) -> None:
    journal[cle] = {"cle": cle, "fichier": fichier, "sha256": empreinte,
                    "ingere_le": maintenant.isoformat(timespec="seconds"), **extra}


def recuperer_box_scores(api, matchs: list[dict], journal: dict, maintenant: datetime,
                         raw: Path = RAW, archive_suivi: Path = ARCHIVE_SUIVI) -> int:
    nouveaux = 0
    for m in matchs:
        cle = f"box-score/{m['match_id']}"
        chemin = raw / "box_scores" / f"{m['match_id']}.json.gz"
        if chemin.exists() or journal.get(cle, {}).get("erreur"):
            continue
        source = archive_suivi / f"{m['match_id']}.json"
        if source.exists():
            payload = json.loads(source.read_text(encoding="utf-8"))
            origine = "archive du suivi de Diomande"
        else:
            try:
                payload = appeler(api, cle)
            except ApiError as exc:
                traiter_erreur(exc, cle, journal, maintenant)
                continue
            origine = "API"
        empreinte = ecrire_gz(chemin, payload)
        _noter(journal, cle, f"box_scores/{m['match_id']}.json.gz", empreinte, maintenant,
               championnat=m["championnat"], vide=_vide(payload), origine=origine)
        nouveaux += 1
    return nouveaux


def lignes_box_score(payload, match_id: int, championnat: str) -> list[dict]:
    """Une ligne par joueur ayant joué, avec les mêmes règles que le suivi :
    comptages absents à 0, xG vide sans tir à 0."""
    lignes = []
    for equipe in payload or []:
        club = equipe.get("team") or {}
        for j in equipe.get("players") or []:
            minutes = _int(j.get("minutesPlayed")) or 0
            if minutes <= 0:
                continue
            s = j.get("statistics") or {}
            ligne = {
                "match_id": match_id, "championnat": championnat, "player_id": j.get("id"),
                "nom": j.get("fullName") or j.get("name"), "club_id": club.get("id"),
                "club": club.get("name"), "poste_match": j.get("position"), "minutes": minutes,
                "titulaire": not j.get("isSubstitute"),
            }
            for col, cle in _COMPTAGES.items():
                ligne[col] = _int(s.get(cle)) or 0
            for col, cle in _DECIMALES.items():
                ligne[col] = _float(s.get(cle))
            if ligne["xg"] is None and ligne["tirs"] == 0:
                ligne["xg"] = 0.0
            lignes.append(ligne)
    return lignes


def lire_box_scores(matchs: list[dict], raw: Path = RAW) -> pd.DataFrame:
    lignes = []
    for m in matchs:
        chemin = raw / "box_scores" / f"{m['match_id']}.json.gz"
        if chemin.exists():
            lignes.extend(lignes_box_score(lire_gz(chemin), m["match_id"], m["championnat"]))
    return pd.DataFrame(lignes)


def _dominant(df: pd.DataFrame, colonne: str) -> pd.Series:
    """Valeur qui totalise le plus de minutes pour chaque joueur."""
    minutes = df.groupby(["player_id", colonne], dropna=False)["minutes"].sum().reset_index()
    minutes = minutes.sort_values(["player_id", "minutes"], ascending=[True, False])
    return minutes.drop_duplicates("player_id").set_index("player_id")[colonne]


def agreger(lignes: pd.DataFrame) -> pd.DataFrame:
    """Totaux de la saison par joueur, sur les matchs des cinq championnats."""
    if lignes.empty:
        return pd.DataFrame()
    g = lignes.groupby("player_id")
    saison = g[["minutes", *STATS]].sum(min_count=1)
    for col in ("xg", "xa"):
        saison[col] = saison[col].fillna(0.0)
        saison[f"{col}_manquants"] = g[col].apply(lambda s: int(s.isna().sum()))
    saison["matchs"] = g.size()
    saison["titularisations"] = g["titulaire"].sum()
    saison["nom"] = g["nom"].last()
    for col in ("club", "championnat", "poste_match"):
        saison[col] = _dominant(lignes, col)
    saison = saison.reset_index()
    return saison[["player_id", "nom", "club", "championnat", "poste_match", "matchs",
                   "titularisations", "minutes", *STATS, "xg_manquants", "xa_manquants"]]


def candidats(saison: pd.DataFrame) -> list[int]:
    if saison.empty:
        return []
    choix = saison[(saison["minutes"] >= MINUTES_MIN) & saison["poste_match"].isin(POSTES_CANDIDATS)]
    return sorted(int(p) for p in choix["player_id"])


# --- 4. Profils ------------------------------------------------------------------
def recuperer_profils(api, ids: list[int], journal: dict, maintenant: datetime, raw: Path = RAW) -> int:
    nouveaux = 0
    for pid in ids:
        cle = f"players/{pid}"
        chemin = raw / "profils" / f"{pid}.json.gz"
        if chemin.exists() or journal.get(cle, {}).get("erreur"):
            continue
        try:
            payload = appeler(api, cle)
        except ApiError as exc:
            traiter_erreur(exc, cle, journal, maintenant)
            continue
        empreinte = ecrire_gz(chemin, payload)
        _noter(journal, cle, f"profils/{pid}.json.gz", empreinte, maintenant)
        nouveaux += 1
    return nouveaux


def lire_profil(payload) -> dict:
    """Poste détaillé du profil /players/{id} (réponse : liste d'un élément)."""
    item = (payload[0] if isinstance(payload, list) and payload else payload) or {}
    profil = item.get("profile") or {}
    poste = profil.get("position") or {}
    return {"poste_principal": poste.get("main"), "postes_secondaires": poste.get("secondary"),
            "pied": profil.get("foot")}


def lire_profils(ids: list[int], raw: Path = RAW) -> pd.DataFrame:
    lignes = []
    for pid in ids:
        chemin = raw / "profils" / f"{pid}.json.gz"
        if chemin.exists():
            lignes.append({"player_id": pid, **lire_profil(lire_gz(chemin))})
    return pd.DataFrame(lignes, columns=["player_id", "poste_principal", "postes_secondaires", "pied"])


# --- Export ----------------------------------------------------------------------
def id_diomande() -> int | None:
    return (lire_json(IDENTIFIANTS_SUIVI, {}) or {}).get("player_id")


def exporter_comparaison(saison: pd.DataFrame, profils: pd.DataFrame, championnats: dict,
                         diomande: int | None) -> list[dict]:
    """Ailiers à 900 minutes et plus : actions par 90 minutes, taux de réussite
    et percentiles parmi eux. Diomande y figure même si son profil le classait ailleurs."""
    df = saison.merge(profils, on="player_id", how="inner")
    est_ailier = df["poste_principal"].isin(POSTES_AILIERS)
    df = df[(df["minutes"] >= MINUTES_MIN) & (est_ailier | (df["player_id"] == diomande))].copy()
    df["xg_xa"] = df["xg"] + df["xa"]
    for col in PAR_90:
        df[f"{col}_90"] = df[col] / df["minutes"] * 90
    for col, (num, den) in TAUX.items():
        df[col] = (df[num] / df[den].where(df[den] > 0)) * 100
    df["buts_moins_xg"] = df["buts"] - df["xg"]
    classes = [f"{c}_90" for c in PAR_90] + list(TAUX) + ["buts_moins_xg"]
    for col in classes:
        df[f"pct_{col}"] = (df[col].rank(pct=True, method="average") * 100).round(0)
    df["poste"] = df["poste_principal"].map(POSTES_AILIERS).fillna(df["poste_principal"])
    df["championnat_nom"] = df["championnat"].map({k: v["nom"] for k, v in championnats.items()})
    df["est_diomande"] = df["player_id"] == diomande
    df["saison"] = SAISON
    for col in [c for c in df.columns if c.endswith("_90") or c.startswith("taux_")] + ["xg", "xa", "xg_xa", "buts_moins_xg"]:
        df[col] = df[col].round(2)
    colonnes = ["player_id", "nom", "club", "championnat", "championnat_nom", "poste", "est_diomande",
                "saison", "matchs", "titularisations", "minutes", "buts", "passes_decisives", "xg", "xa",
                "xg_xa", "buts_moins_xg", *[f"{c}_90" for c in PAR_90], *TAUX,
                *[f"pct_{c}" for c in classes]]
    df = df.sort_values(["est_diomande", "xg_xa_90"], ascending=[False, False])
    return json.loads(df[colonnes].to_json(orient="records", force_ascii=False))


# --- Orchestration ----------------------------------------------------------------
def etat(matchs: list[dict], ids_candidats: list[int] | None, journal: dict, raw: Path = RAW) -> dict:
    archives = {int(p.name.split(".")[0]) for p in (raw / "box_scores").glob("*.json.gz")}
    erreurs = {int(k.split("/")[1]) for k, v in journal.items() if k.startswith("box-score/") and v.get("erreur")}
    vides = sum(1 for k, v in journal.items() if k.startswith("box-score/") and v.get("vide"))
    par_championnat = {}
    for m in matchs:
        c = par_championnat.setdefault(m["championnat"], {"matchs_termines": 0, "box_scores": 0})
        c["matchs_termines"] += 1
        c["box_scores"] += m["match_id"] in archives
    box_complets = all(m["match_id"] in archives or m["match_id"] in erreurs for m in matchs) and bool(matchs)
    profils = {int(p.name.split(".")[0]) for p in (raw / "profils").glob("*.json.gz")}
    erreurs_profils = {int(k.split("/")[1]) for k, v in journal.items() if k.startswith("players/") and v.get("erreur")}
    profils_complets = box_complets and ids_candidats is not None and all(
        p in profils or p in erreurs_profils for p in ids_candidats)
    return {
        "saison": SAISON,
        "box_scores": {"archives": len(archives & {m["match_id"] for m in matchs}), "total": len(matchs),
                       "vides": vides, "erreurs": len(erreurs)},
        "par_championnat": par_championnat,
        "profils": {"archives": len(profils), "candidats": None if ids_candidats is None else len(ids_candidats),
                    "erreurs": len(erreurs_profils)},
        "complet": profils_complets,
    }


def construire(championnats: dict, matchs: list[dict], journal: dict, raw: Path = RAW, out: Path = OUT,
               sortie: Path = SORTIE, diomande: int | None = None) -> dict:
    """Tables et export. L'export n'est écrit qu'une fois la collecte complète."""
    saison = agreger(lire_box_scores(matchs, raw))
    e = etat(matchs, None, journal, raw)
    ids = candidats(saison) if e["box_scores"]["archives"] + e["box_scores"]["erreurs"] >= len(matchs) else None
    e = etat(matchs, ids, journal, raw)
    if e["complet"]:
        profils = lire_profils(ids, raw)
        out.mkdir(parents=True, exist_ok=True)
        saison.to_csv(out / "fact_joueur_saison.csv", index=False, encoding="utf-8")
        profils.to_csv(out / "dim_joueur.csv", index=False, encoding="utf-8")
        lignes = exporter_comparaison(saison, profils, championnats, diomande)
        ecrire_json(sortie, lignes)
        e["ailiers"] = len(lignes)
        dire(f"[OK] Collecte complète : {len(lignes)} ailiers à {MINUTES_MIN} minutes et plus, "
             f"écrits dans {sortie.relative_to(ROOT) if sortie.is_relative_to(ROOT) else sortie}")
    return e


def collecter(api, raw: Path = RAW, out: Path = OUT, sortie: Path = SORTIE,
              archive_suivi: Path = ARCHIVE_SUIVI, maintenant: datetime | None = None,
              diomande: int | None = None) -> dict:
    maintenant = maintenant or datetime.now(timezone.utc)
    journal = {e["cle"]: e for e in lire_json(raw / "journal.json", []) or []}
    championnats, matchs, arret = {}, [], None
    try:
        championnats = resoudre_championnats(api, raw)
        matchs = recuperer_calendriers(api, championnats, raw)
        # Vérification du poste détaillé dès le premier passage, sur Diomande
        if diomande and not (raw / "profils" / f"{diomande}.json.gz").exists():
            recuperer_profils(api, [diomande], journal, maintenant, raw)
            chemin = raw / "profils" / f"{diomande}.json.gz"
            if chemin.exists():
                p = lire_profil(lire_gz(chemin))
                dire(f"[OK] Profil de Diomande : poste principal « {p['poste_principal']} », "
                     f"secondaire « {p['postes_secondaires']} »")
        recuperer_box_scores(api, matchs, journal, maintenant, raw, archive_suivi)
        e = etat(matchs, None, journal, raw)
        if e["box_scores"]["archives"] + e["box_scores"]["erreurs"] >= len(matchs):
            saison = agreger(lire_box_scores(matchs, raw))
            recuperer_profils(api, candidats(saison), journal, maintenant, raw)
    except Arret as exc:
        arret = str(exc)
    finally:
        ecrire_json(raw / "journal.json", sorted(journal.values(), key=lambda x: x["cle"]))
    e = construire(championnats, matchs, journal, raw, out, sortie, diomande) if matchs else {"complet": False}
    e.update(maj=maintenant.isoformat(timespec="seconds"), requetes=api.nb_requetes,
             quota_restant=api.quota_restant, arret=arret)
    ecrire_json(raw / "progression.json", e)
    b, p = e.get("box_scores", {}), e.get("profils", {})
    dire(f"Box scores : {b.get('archives')} sur {b.get('total')} ({b.get('vides')} vides). "
         f"Profils : {p.get('archives')} sur {p.get('candidats') if p.get('candidats') is not None else 'à déterminer'}. "
         f"Requêtes : {api.nb_requetes}, quota restant : {api.quota_restant}."
         + (f" Arrêt : {arret}." if arret else ""))
    return e


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    diomande = id_diomande()
    if "--hors-ligne" in sys.argv:
        championnats = lire_json(RAW / "championnats.json", {}) or {}
        matchs = []
        for code in championnats:
            archive = lire_json(RAW / "calendriers" / f"{code}.json") or {"matchs": []}
            matchs += matchs_retenus(archive["matchs"], code)
        journal = {e["cle"]: e for e in lire_json(RAW / "journal.json", []) or []}
        print(json.dumps(construire(championnats, matchs, journal, diomande=diomande), ensure_ascii=False, indent=1))
        return 0
    try:
        api = Highlightly()
        collecter(api, diomande=diomande)
    except ApiError as exc:
        dire(f"[ÉCHEC] {exc}", "error")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
