"""Govee's public scene library: names, codes and parameter blobs per SKU.

One anonymous request per SKU, kept on disk. After that every scene is
activated locally. If the library cannot be reached the integration falls back
to the few scenes the stock library knows.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CATALOG_MAX_AGE,
    CATALOG_STORE_VERSION,
    DOMAIN,
    SCENE_LIBRARY_APP_VERSION,
    SCENE_LIBRARY_URL,
    SCENE_LIBRARY_USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class LibraryScene:
    """A scene of the public library."""

    name: str
    code: int
    param: bytes


def parse_library(payload: dict[str, Any]) -> list[LibraryScene]:
    """Turn the library response into scenes with unique, clean names."""
    scenes: list[LibraryScene] = []
    seen: dict[str, int] = {}
    for category in payload.get("data", {}).get("categories", []):
        for scene in category.get("scenes", []):
            effects = scene.get("lightEffects") or []
            if not effects:
                continue
            effect = effects[0]
            name = " ".join(str(scene.get("sceneName", "")).replace("\xa0", " ").split())
            code = effect.get("sceneCode", scene.get("sceneCode"))
            if not name or not isinstance(code, int):
                continue
            seen[name] = seen.get(name, 0) + 1
            if seen[name] > 1:
                name = f"{name} {seen[name]}"
            param = base64.b64decode(effect.get("scenceParam") or "")
            scenes.append(LibraryScene(name, code, param))
    return scenes


def _to_store(scenes: list[LibraryScene]) -> list[dict[str, Any]]:
    return [
        {"name": s.name, "code": s.code, "param": base64.b64encode(s.param).decode()}
        for s in scenes
    ]


def _from_store(rows: list[dict[str, Any]]) -> list[LibraryScene]:
    return [
        LibraryScene(row["name"], row["code"], base64.b64decode(row["param"]))
        for row in rows
    ]


class SceneCatalog:
    """Loads and caches the scene library per SKU."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._scenes: dict[str, list[LibraryScene]] = {}

    async def async_get(self, sku: str) -> list[LibraryScene]:
        """Return the scenes of a SKU (memory, then disk, then network)."""
        if sku in self._scenes:
            return self._scenes[sku]

        store: Store[dict[str, Any]] = Store(
            self._hass, CATALOG_STORE_VERSION, f"{DOMAIN}.catalog.{sku}"
        )
        stored = await store.async_load()
        fresh = False
        if stored:
            fetched = dt_util.parse_datetime(stored.get("fetched", ""))
            fresh = fetched is not None and dt_util.utcnow() - fetched < CATALOG_MAX_AGE
            self._scenes[sku] = _from_store(stored["scenes"])
        if fresh:
            return self._scenes[sku]

        try:
            scenes = await self._async_fetch(sku)
        except (TimeoutError, OSError, ValueError) as err:
            _LOGGER.warning(
                "Scene library for %s not available (%s); using %s",
                sku,
                err,
                "the cached copy" if stored else "built-in scenes only",
            )
            self._scenes.setdefault(sku, [])
            return self._scenes[sku]

        self._scenes[sku] = scenes
        await store.async_save({"fetched": dt_util.utcnow().isoformat(), "scenes": _to_store(scenes)})
        return scenes

    async def _async_fetch(self, sku: str) -> list[LibraryScene]:
        session = async_get_clientsession(self._hass)
        async with session.get(
            SCENE_LIBRARY_URL,
            params={"sku": sku},
            headers={
                "AppVersion": SCENE_LIBRARY_APP_VERSION,
                "User-Agent": SCENE_LIBRARY_USER_AGENT,
            },
            timeout=timedelta(seconds=15).total_seconds(),
        ) as response:
            response.raise_for_status()
            payload = await response.json(content_type=None)
        scenes = parse_library(payload)
        if not scenes:
            raise ValueError("library response contains no scenes")
        return scenes
