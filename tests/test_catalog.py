"""Catalog cache: the library must survive without internet."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.govee_local_plus.catalog import (
    LibraryScene,
    SceneCatalog,
    _to_store,
)
from custom_components.govee_local_plus.const import (
    CATALOG_MAX_AGE,
    CATALOG_STORE_VERSION,
    DOMAIN,
)

SCENES = [LibraryScene("Aurora", 12238, b"\x01\x02")]


async def _prime_store(hass: HomeAssistant, age: timedelta) -> None:
    from homeassistant.helpers.storage import Store

    store = Store(hass, CATALOG_STORE_VERSION, f"{DOMAIN}.catalog.H61E5")
    await store.async_save(
        {"fetched": (dt_util.utcnow() - age).isoformat(), "scenes": _to_store(SCENES)}
    )


async def test_expired_cache_is_kept_when_offline(hass: HomeAssistant) -> None:
    await _prime_store(hass, CATALOG_MAX_AGE + timedelta(days=400))
    catalog = SceneCatalog(hass)
    with patch.object(catalog, "_async_fetch", AsyncMock(side_effect=OSError("offline"))):
        assert await catalog.async_get("H61E5") == SCENES


async def test_fresh_cache_does_not_touch_the_network(hass: HomeAssistant) -> None:
    await _prime_store(hass, timedelta(days=1))
    catalog = SceneCatalog(hass)
    fetch = AsyncMock()
    with patch.object(catalog, "_async_fetch", fetch):
        assert await catalog.async_get("H61E5") == SCENES
    fetch.assert_not_awaited()


async def test_no_cache_and_offline_gives_empty_list(hass: HomeAssistant) -> None:
    catalog = SceneCatalog(hass)
    with patch.object(catalog, "_async_fetch", AsyncMock(side_effect=OSError("offline"))):
        assert await catalog.async_get("H61E5") == []
