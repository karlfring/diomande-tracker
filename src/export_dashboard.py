"""Assemble les tables de toutes les saisons en un seul fichier pour le
tableau de bord : une ligne par match officiel terminé.

Usage :
    python src/export_dashboard.py      -> data/dashboard/matchs.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import DATA_OUT_ROOT, ROOT  # noqa: E402

SORTIE = ROOT / "data" / "dashboard" / "matchs.json"

COLONNES = [
    "match_id", "saison", "club", "numero_match", "date", "competition", "adversaire",
    "domicile", "score", "resultat", "statut", "minutes", "note", "buts",
    "passes_decisives", "xg", "xa", "tirs", "tirs_cadres", "passes_cles",
    "passes", "passes_reussies", "dribbles_tentes", "dribbles_reussis",
    "duels", "duels_gagnes", "fautes_subies", "cartons_jaunes", "expulse",
]


def assembler(racine: Path = DATA_OUT_ROOT) -> pd.DataFrame:
    morceaux = []
    for dossier in sorted(p for p in racine.iterdir() if p.is_dir() and p.name.isdigit()):
        matchs = pd.read_csv(dossier / "dim_match.csv")
        fait = pd.read_csv(dossier / "fact_joueur_match.csv")
        df = fait.merge(matchs, on="match_id", how="inner")
        df = df[df["officiel"]]
        df["saison"] = int(dossier.name)
        df["date"] = df["date_utc"].str.slice(0, 10)
        df["score"] = df["buts_pour"].astype("Int64").astype(str) + " - " + df["buts_contre"].astype("Int64").astype(str)
        df["domicile"] = df["domicile"].map({True: "Domicile", False: "Extérieur"})
        morceaux.append(df[COLONNES])
    tout = pd.concat(morceaux, ignore_index=True).sort_values(["saison", "date"])
    for col in ("xg", "xa", "note"):
        tout[col] = tout[col].round(2)
    entiers = [c for c in COLONNES[COLONNES.index("buts"):] if c not in ("xg", "xa", "expulse")]
    tout[["minutes", *entiers]] = tout[["minutes", *entiers]].astype("Int64")
    return tout


def main() -> int:
    df = assembler()
    SORTIE.parent.mkdir(parents=True, exist_ok=True)
    lignes = json.loads(df.to_json(orient="records", force_ascii=False))
    SORTIE.write_text(json.dumps(lignes, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(lignes)} matchs écrits dans {SORTIE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
