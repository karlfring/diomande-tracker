"""Tests du commentaire IA, sans réseau : l'API est remplacée par une fausse réponse."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import commentaire_ia as ia  # noqa: E402

MATCH = {
    "match_id": 42, "saison": 2026, "club": "Real Madrid", "date": "2026-10-18",
    "competition": "La Liga", "adversaire": "Sevilla FC", "domicile": "Domicile",
    "score": "3 - 1", "resultat": "V", "statut": "titulaire", "minutes": 90,
    "note": 9.02, "buts": 1, "passes_decisives": 0, "xg": 0.23, "xa": 0.24, "tirs": 3,
    "tirs_cadres": 2, "passes_cles": 2, "dribbles_reussis": 3, "dribbles_tentes": 5,
    "duels_gagnes": 7, "duels": 11,
}
BANC = {**MATCH, "match_id": 41, "date": "2026-10-01", "statut": "sur le banc", "minutes": 0}


class FausseReponse:
    def __init__(self, contenu, code=200):
        self.status_code = code
        self.text = str(contenu)
        self._json = {"choices": [{"message": {"content": contenu}}], "usage": {"total_tokens": 321}}

    def json(self):
        return self._json


class FausseSession:
    def __init__(self, contenu, code=200, codes=None):
        self.contenu, self.code, self.corps = contenu, code, None
        self.codes, self.modeles = list(codes or []), []

    def post(self, url, json=None, **kwargs):
        self.corps = dict(json)
        self.modeles.append(json["model"])
        code = self.codes.pop(0) if self.codes else self.code
        return FausseReponse(self.contenu, code)


@pytest.fixture(autouse=True)
def sans_attente(monkeypatch):
    monkeypatch.setattr(ia, "ATTENTE_S", 0)


def reponse(commentaire, chiffres=()):
    return json.dumps({"titre": "Un but contre Séville", "commentaire": commentaire,
                       "chiffres_cites": [{"libelle": l, "valeur": v} for l, v in chiffres]})


# --- faits -----------------------------------------------------------------
def test_faits_sans_statistiques_quand_il_n_a_pas_joue():
    faits = ia.faits_du_match(BANC)
    assert faits["minutes"] == 0
    assert "buts" not in faits and "note" not in faits


def test_dernier_match_joue_par_defaut():
    assert ia.choisir_match([MATCH, BANC], None)["match_id"] == 42


# --- vérification ----------------------------------------------------------
def test_commentaire_fidele_valide():
    faits = ia.faits_du_match(MATCH)
    contenu = json.loads(reponse(
        "Titulaire le 18 octobre 2026, il a joué 90 minutes et marqué 1 but pour 0,23 xG, avec une note de 9,02.",
        [("buts", 1), ("xG", "0,23"), ("note", 9.02)]))
    assert ia.verifier(contenu, faits) == {"valide": True, "problemes": []}


def test_chiffre_invente_detecte():
    faits = ia.faits_du_match(MATCH)
    contenu = json.loads(reponse("Il a tenté 6 dribbles et marqué 2 buts.", [("buts", 2)]))
    verif = ia.verifier(contenu, faits)
    assert not verif["valide"]
    assert any("6" in p for p in verif["problemes"])
    assert any("buts = 2, attendu 1" in p for p in verif["problemes"])


def test_arrondi_non_autorise():
    faits = ia.faits_du_match(MATCH)
    contenu = json.loads(reponse("Il obtient une note de 9."))
    assert not ia.verifier(contenu, faits)["valide"]


# --- appel et archive ------------------------------------------------------
def test_generer_archive_et_verifie(tmp_path, monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "test")
    fichier = tmp_path / "matchs.json"
    fichier.write_text(json.dumps([MATCH, BANC]), encoding="utf-8")
    session = FausseSession(reponse("Il a marqué 1 but en 90 minutes.", [("buts", 1)]))
    a = ia.generer(None, session=session, sortie=tmp_path / "out", matchs_path=fichier)
    assert a["verification"]["valide"]
    assert (tmp_path / "out" / "42.json").exists()
    assert session.corps["response_format"] == {"type": "json_object"}
    assert "Sevilla FC" in session.corps["messages"][1]["content"]


def test_reponse_non_json_refusee(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "test")
    with pytest.raises(ia.ErreurIA, match="JSON"):
        ia.appeler_modele({"a": 1}, session=FausseSession("pas du json"))


def test_cle_absente(monkeypatch):
    for nom in ("MISTRAL_API_KEY", "GEMINI_API_KEY", "LLM_FOURNISSEUR"):
        monkeypatch.delenv(nom, raising=False)
    with pytest.raises(ia.ErreurIA, match="Clé absente"):
        ia.appeler_modele({"a": 1}, session=FausseSession("{}"))


def test_erreur_http(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "test")
    with pytest.raises(ia.ErreurIA, match="401"):
        ia.appeler_modele({"a": 1}, session=FausseSession("Unauthorized", code=401))


def test_fournisseur_choisi_selon_la_cle(monkeypatch):
    monkeypatch.delenv("LLM_FOURNISSEUR", raising=False)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    session = FausseSession(reponse("Il a marqué 1 but."))
    r = ia.appeler_modele({"buts": 1}, session=session)
    assert r["fournisseur"] == "gemini"
    assert session.corps["model"] == "gemini-flash-latest"


def test_surcharge_puis_succes(monkeypatch):
    monkeypatch.setenv("LLM_FOURNISSEUR", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    session = FausseSession(reponse("Il a marqué 1 but."), codes=[503, 503])
    r = ia.appeler_modele({"buts": 1}, session=session)
    assert session.modeles == ["gemini-flash-latest"] * 3
    assert r["modele"] == "gemini-flash-latest"


def test_bascule_sur_le_modele_de_secours(monkeypatch):
    monkeypatch.setenv("LLM_FOURNISSEUR", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    session = FausseSession(reponse("Il a marqué 1 but."), codes=[503, 503, 503])
    r = ia.appeler_modele({"buts": 1}, session=session)
    assert session.modeles[-1] == "gemini-flash-lite-latest"
    assert r["modele"] == "gemini-flash-lite-latest"


def test_toujours_indisponible(monkeypatch):
    monkeypatch.setenv("LLM_FOURNISSEUR", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    with pytest.raises(ia.ErreurIA, match="indisponible"):
        ia.appeler_modele({"buts": 1}, session=FausseSession("{}", code=503))
