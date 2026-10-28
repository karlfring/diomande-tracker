"""Commentaire de match rédigé par un modèle de langage, puis vérifié.

Usage :
    export GEMINI_API_KEY=ta_cle         (PowerShell : $env:GEMINI_API_KEY="ta_cle")
    (ou MISTRAL_API_KEY ; LLM_FOURNISSEUR=gemini|mistral force le choix)
    python src/commentaire_ia.py                     # dernier match joué
    python src/commentaire_ia.py --match 1336360124  # un match précis

Étapes :
  1. lit les statistiques du match dans data/dashboard/matchs.json ;
  2. envoie au modèle ces faits, et uniquement eux, avec une consigne stricte ;
  3. demande une réponse en JSON (titre, commentaire, chiffres cités) ;
  4. vérifie que chaque nombre écrit par le modèle existe dans les faits
     fournis : un chiffre inventé fait échouer la vérification ;
  5. archive le tout dans data/ia/commentaires/<match_id>.json (consigne,
     faits, réponse brute, résultat de la vérification).

Seules des statistiques sportives publiques sont envoyées au modèle.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import ROOT  # noqa: E402

# Deux fournisseurs possibles, avec la même interface de type « chat
# completions » : le code ne dépend pas d'un seul fournisseur de modèle.
FOURNISSEURS = {
    "gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "cle": "GEMINI_API_KEY",
        "modele": "gemini-flash-latest",
        "secours": ["gemini-flash-lite-latest"],
    },
    "mistral": {
        "url": "https://api.mistral.ai/v1/chat/completions",
        "cle": "MISTRAL_API_KEY",
        "modele": "mistral-small-latest",
        "secours": [],
    },
}


def fournisseur() -> tuple[str, dict]:
    """Le fournisseur choisi par LLM_FOURNISSEUR, sinon le premier dont la clé existe."""
    nom = os.environ.get("LLM_FOURNISSEUR")
    if not nom:
        nom = next((n for n, f in FOURNISSEURS.items() if os.environ.get(f["cle"])), "gemini")
    if nom not in FOURNISSEURS:
        raise ErreurIA(f"Fournisseur inconnu : {nom} (choix : {', '.join(FOURNISSEURS)}).")
    f = dict(FOURNISSEURS[nom])
    f["modele"] = os.environ.get("LLM_MODELE", f["modele"])
    return nom, f

TEMPERATURE = 0.2          # faible : on veut un texte fidèle, pas créatif
MAX_TOKENS = 2048          # marge pour les modèles qui « réfléchissent » avant de répondre
TENTATIVES = 3             # par modèle, en cas de surcharge ou de limite de débit
ATTENTE_S = 10             # attente avant la 2e tentative, doublée ensuite
CODES_TEMPORAIRES = {429, 500, 502, 503, 504}
MATCHS = ROOT / "data" / "dashboard" / "matchs.json"
SORTIE = ROOT / "data" / "ia" / "commentaires"

MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]

CONSIGNE = """Tu es analyste football pour un tableau de bord de suivi de joueur.
Rédige un court commentaire en français sur la performance de Yan Diomande lors du match décrit.

Règles :
- Utilise uniquement les faits fournis. N'invente aucun chiffre, aucun nom, aucun événement.
- Dans le commentaire, écris les nombres en chiffres, exactement comme dans les faits, avec une virgule pour les décimales (0,23).
- Dans "chiffres_cites", chaque "valeur" est un nombre JSON avec un point pour les décimales (0.23).
- 3 phrases au plus, ton neutre et factuel, sans superlatifs.
- S'il n'a pas joué, dis-le simplement.

Réponds uniquement avec un objet JSON de cette forme :
{"titre": "...", "commentaire": "...", "chiffres_cites": [{"libelle": "...", "valeur": 0}]}"""


class ErreurIA(RuntimeError):
    """Clé absente, réponse invalide ou refus de l'API."""


# --- 1. Les faits ------------------------------------------------------------
def date_fr(iso: str) -> str:
    a, m, j = (int(x) for x in iso[:10].split("-"))
    return f"{j} {MOIS[m - 1]} {a}"


def faits_du_match(m: dict) -> dict:
    """Les seules informations que le modèle a le droit d'utiliser."""
    a_joue = (m.get("minutes") or 0) > 0
    faits = {
        "date": date_fr(m["date"]),
        "club": m["club"],
        "competition": m["competition"],
        "adversaire": m["adversaire"],
        "lieu": m["domicile"],
        "score": m["score"],
        "resultat": {"V": "victoire", "N": "match nul", "D": "défaite"}.get(m.get("resultat"), "inconnu"),
        "statut": m["statut"],
        "minutes": m.get("minutes") or 0,
    }
    if a_joue:
        for cle in ("note", "buts", "passes_decisives", "xg", "xa", "tirs", "tirs_cadres",
                    "passes_cles", "dribbles_reussis", "dribbles_tentes", "duels_gagnes", "duels"):
            faits[cle] = m.get(cle)
    return faits


def choisir_match(matchs: list[dict], match_id: int | None) -> dict:
    if match_id is not None:
        for m in matchs:
            if int(m["match_id"]) == match_id:
                return m
        raise ErreurIA(f"Match {match_id} introuvable dans {MATCHS.name}.")
    joues = [m for m in matchs if (m.get("minutes") or 0) > 0]
    if not joues:
        raise ErreurIA("Aucun match joué dans les données.")
    return max(joues, key=lambda m: m["date"])


# --- 2. L'appel au modèle -----------------------------------------------------
def appeler_modele(faits: dict, session: requests.Session | None = None, cle: str | None = None) -> dict:
    nom, f = fournisseur()
    cle = cle or os.environ.get(f["cle"])
    if not cle:
        raise ErreurIA(f"Clé absente : définis la variable d'environnement {f['cle']} ({nom}).")
    corps = {
        "model": f["modele"],
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": CONSIGNE},
            {"role": "user", "content": "Faits du match :\n" + json.dumps(faits, ensure_ascii=False, indent=1)},
        ],
    }
    s = session or requests.Session()
    entetes = {"Authorization": f"Bearer {cle}", "Content-Type": "application/json"}
    erreurs = []
    # Un modèle surchargé (503) ou une limite de débit (429) sont passagers :
    # on réessaie en espaçant les appels, puis on passe au modèle de secours.
    for modele in [f["modele"], *f.get("secours", [])]:
        corps["model"] = modele
        for tentative in range(TENTATIVES):
            if tentative:
                time.sleep(ATTENTE_S * 2 ** (tentative - 1))
            try:
                r = s.post(f["url"], json=corps, timeout=60, headers=entetes)
            except requests.RequestException as exc:
                erreurs.append(f"{modele} : {exc}")
                continue
            if r.status_code in CODES_TEMPORAIRES:
                erreurs.append(f"{modele} : HTTP {r.status_code}")
                continue
            if r.status_code >= 400:
                raise ErreurIA(f"HTTP {r.status_code} : {r.text[:300]}")
            f["modele"] = modele
            break
        else:
            continue
        break
    else:
        raise ErreurIA("Modèle indisponible après plusieurs tentatives : " + " ; ".join(erreurs))
    reponse = r.json()
    choix = reponse["choices"][0]
    texte = choix["message"]["content"] or ""
    contenu = lire_json(texte)
    if contenu is None:
        raise ErreurIA(f"Le modèle n'a pas renvoyé de JSON valide (arrêt : {choix.get('finish_reason')}) : {texte[:1500]}")
    return {"contenu": contenu, "brut": reponse, "fournisseur": nom, "modele": f["modele"]}


def lire_json(texte: str) -> dict | None:
    """Lit l'objet JSON de la réponse, même entouré de ```json ... ``` ou de texte."""
    texte = texte.strip()
    if texte.startswith("```"):
        texte = re.sub(r"^```(?:json)?\s*|\s*```$", "", texte)
    # Réparation d'une erreur fréquente : « "valeur": 6,32 » au lieu de 6.32
    texte = re.sub(r'("valeur"\s*:\s*-?\d+),(\d+)', r"\1.\2", texte)
    try:
        return json.loads(texte)
    except json.JSONDecodeError:
        debut, fin = texte.find("{"), texte.rfind("}")
        if debut != -1 and fin > debut:
            try:
                return json.loads(texte[debut:fin + 1])
            except json.JSONDecodeError:
                return None
        return None


# --- 3. La vérification -------------------------------------------------------
_NOMBRE = re.compile(r"\d+(?:[.,]\d+)?")


def _nombres(texte: str) -> list[float]:
    return [float(x.replace(",", ".")) for x in _NOMBRE.findall(texte or "")]


def nombres_autorises(faits: dict) -> set[float]:
    """Tous les nombres présents dans les faits, y compris dans le score et la date."""
    autorises: set[float] = set()
    for v in faits.values():
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, (int, float)):
            autorises.add(round(float(v), 2))
        else:
            autorises.update(round(n, 2) for n in _nombres(str(v)))
    return autorises


# Libellé lu dans la réponse -> champ des faits. Les libellés les plus précis
# d'abord : « tirs cadrés » avant « tirs », « passes décisives » avant « passes ».
_LIBELLES = [
    ("tir cadre", "tirs_cadres"), ("tirs cadre", "tirs_cadres"), ("tir", "tirs"),
    ("passe decisive", "passes_decisives"), ("passes decisive", "passes_decisives"),
    ("passe cle", "passes_cles"), ("passes cle", "passes_cles"),
    ("dribbles tente", "dribbles_tentes"), ("dribble", "dribbles_reussis"),
    ("duels gagne", "duels_gagnes"), ("duel", "duels"),
    ("xg", "xg"), ("xa", "xa"), ("note", "note"), ("minute", "minutes"), ("but", "buts"),
]


def _champ(libelle: str) -> str | None:
    l = unicodedata.normalize("NFKD", str(libelle)).encode("ascii", "ignore").decode().lower()
    for motif, champ in _LIBELLES:
        if motif in l:
            return champ
    return None


def verifier(contenu: dict, faits: dict) -> dict:
    """Chaque nombre écrit par le modèle doit exister dans les faits fournis, et
    chaque chiffre cité avec un libellé reconnu doit valoir exactement le fait
    correspondant (« 2 buts » est refusé si le joueur en a marqué 1)."""
    autorises = nombres_autorises(faits)
    problemes = []
    if not isinstance(contenu.get("commentaire"), str) or not contenu["commentaire"].strip():
        problemes.append("commentaire absent")
    texte = f"{contenu.get('titre', '')} {contenu.get('commentaire', '')}"
    for n in _nombres(texte):
        if round(n, 2) not in autorises:
            problemes.append(f"nombre absent des faits dans le texte : {n:g}")
    for c in contenu.get("chiffres_cites") or []:
        try:
            v = round(float(str(c.get("valeur")).replace(",", ".")), 2)
        except (TypeError, ValueError):
            problemes.append(f"valeur illisible : {c}")
            continue
        champ = _champ(c.get("libelle", ""))
        if champ in faits and faits[champ] is not None:
            if v != round(float(faits[champ]), 2):
                problemes.append(f"chiffre cité faux : {c.get('libelle')} = {v:g}, attendu {faits[champ]}")
        elif v not in autorises:
            problemes.append(f"chiffre cité absent des faits : {c.get('libelle')} = {v:g}")
    return {"valide": not problemes, "problemes": problemes}


# --- 4. Orchestration ---------------------------------------------------------
def generer(match_id: int | None = None, session: requests.Session | None = None,
            sortie: Path = SORTIE, matchs_path: Path = MATCHS) -> dict:
    matchs = json.loads(matchs_path.read_text(encoding="utf-8"))
    match = choisir_match(matchs, match_id)
    faits = faits_du_match(match)
    resultat = appeler_modele(faits, session=session)
    verif = verifier(resultat["contenu"], faits)
    archive = {
        "match_id": match["match_id"],
        "genere_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fournisseur": resultat["fournisseur"],
        "modele": resultat["modele"],
        "temperature": TEMPERATURE,
        "consigne": CONSIGNE,
        "faits": faits,
        "reponse": resultat["contenu"],
        "verification": verif,
        "usage": resultat["brut"].get("usage"),
    }
    sortie.mkdir(parents=True, exist_ok=True)
    (sortie / f"{match['match_id']}.json").write_text(json.dumps(archive, ensure_ascii=False, indent=1), encoding="utf-8")
    return archive


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--match", type=int, default=None, help="identifiant du match (défaut : dernier match joué)")
    args = p.parse_args()
    try:
        a = generer(args.match)
    except ErreurIA as exc:
        print(f"[ÉCHEC] {exc}")
        return 1
    f = a["faits"]
    print(f"{f['date']}, {f['competition']}, {f['adversaire']} ({f['score']})")
    print(f"\n{a['reponse'].get('titre', '')}\n{a['reponse'].get('commentaire', '')}\n")
    if a["verification"]["valide"]:
        print("[OK] Vérification : tous les chiffres viennent des faits fournis.")
        return 0
    print("[ALERTE] Vérification échouée :")
    for pb in a["verification"]["problemes"]:
        print(f"  - {pb}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
