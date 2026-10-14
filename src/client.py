"""Client minimal pour API-Football : clé, délais, quota, erreurs."""

from __future__ import annotations

import logging
import os
import time

import requests

from config import (
    API_BASE,
    API_KEY_ENV,
    MAX_RETRIES,
    MIN_DAILY_QUOTA,
    REQUEST_DELAY_S,
    REQUEST_TIMEOUT_S,
)

log = logging.getLogger("client")


class ApiError(RuntimeError):
    """Erreur renvoyée par l'API (clé invalide, saison non accessible...)."""


class QuotaExhausted(RuntimeError):
    """Le quota journalier est presque épuisé : on arrête proprement."""


class ApiFootball:
    def __init__(self, api_key: str | None = None, session: requests.Session | None = None):
        key = api_key or os.environ.get(API_KEY_ENV)
        if not key:
            raise ApiError(
                f"Clé absente : définis la variable d'environnement {API_KEY_ENV}."
            )
        self.session = session or requests.Session()
        self.session.headers.update({"x-apisports-key": key})
        self._dernier_appel = 0.0
        self.quota_restant: int | None = None

    def _attendre(self) -> None:
        ecoule = time.monotonic() - self._dernier_appel
        if ecoule < REQUEST_DELAY_S:
            time.sleep(REQUEST_DELAY_S - ecoule)

    def get(self, endpoint: str, **params) -> dict:
        """Appelle un endpoint et renvoie le JSON complet.

        API-Football répond 200 même en cas d'erreur métier : les erreurs
        sont dans le champ `errors` (liste vide ou dictionnaire).
        """
        if self.quota_restant is not None and self.quota_restant <= MIN_DAILY_QUOTA:
            raise QuotaExhausted(f"Quota journalier presque épuisé ({self.quota_restant}).")

        url = f"{API_BASE}/{endpoint.lstrip('/')}"
        for tentative in range(1, MAX_RETRIES + 1):
            self._attendre()
            self._dernier_appel = time.monotonic()
            try:
                r = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT_S)
            except requests.RequestException as exc:
                log.warning("tentative %s/%s : %s", tentative, MAX_RETRIES, exc)
                continue
            restant = r.headers.get("x-ratelimit-requests-remaining")
            if restant is not None and restant.isdigit():
                self.quota_restant = int(restant)
            if r.status_code == 429:
                log.warning("limite par minute atteinte, nouvelle tentative")
                time.sleep(60)
                continue
            r.raise_for_status()
            payload = r.json()
            erreurs = payload.get("errors")
            if erreurs:
                raise ApiError(f"{endpoint} {params} : {erreurs}")
            return payload
        raise ApiError(f"{endpoint} {params} : échec après {MAX_RETRIES} tentatives")
