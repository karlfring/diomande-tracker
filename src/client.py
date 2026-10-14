"""Client minimal pour l'API Highlightly : clé, délais, quota, erreurs."""

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
    """Erreur renvoyée par l'API (clé invalide, ressource introuvable...)."""


class QuotaExhausted(RuntimeError):
    """Le quota journalier est épuisé ou presque : on arrête proprement."""


class Highlightly:
    def __init__(self, api_key: str | None = None, session: requests.Session | None = None):
        key = api_key or os.environ.get(API_KEY_ENV)
        if not key:
            raise ApiError(f"Clé absente : définis la variable d'environnement {API_KEY_ENV}.")
        self.session = session or requests.Session()
        self.session.headers.update({"x-rapidapi-key": key})
        self._dernier_appel = 0.0
        self.quota_restant: int | None = None
        self.nb_requetes = 0

    def _attendre(self) -> None:
        ecoule = time.monotonic() - self._dernier_appel
        if ecoule < REQUEST_DELAY_S:
            time.sleep(REQUEST_DELAY_S - ecoule)

    def get(self, endpoint: str, **params):
        """Appelle un endpoint et renvoie le JSON (objet ou liste)."""
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
            self.nb_requetes += 1
            restant = r.headers.get("x-ratelimit-requests-remaining")
            if restant is not None and str(restant).isdigit():
                self.quota_restant = int(restant)
            if r.status_code == 429:
                raise QuotaExhausted("Quota journalier épuisé (HTTP 429).")
            if r.status_code >= 500:
                log.warning("tentative %s/%s : HTTP %s", tentative, MAX_RETRIES, r.status_code)
                time.sleep(5)
                continue
            if r.status_code >= 400:
                raise ApiError(f"{endpoint} {params} : HTTP {r.status_code} {r.text[:300]}")
            return r.json()
        raise ApiError(f"{endpoint} {params} : échec après {MAX_RETRIES} tentatives")

    def get_pagine(self, endpoint: str, **params) -> tuple[list, dict]:
        """Parcourt toutes les pages d'un endpoint paginé.

        Renvoie (éléments, plan). Le bloc `plan` signale ce que l'offre
        gratuite masque éventuellement.
        """
        elements, offset, plan = [], 0, {}
        while True:
            page = self.get(endpoint, offset=offset, **params)
            donnees = page.get("data", [])
            plan = page.get("plan") or plan
            elements.extend(donnees)
            total = (page.get("pagination") or {}).get("totalCount", len(elements))
            offset += len(donnees)
            if not donnees or offset >= total:
                return elements, plan
