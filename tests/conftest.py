"""Shared test fixtures (paths to the committed tiny DEM / OSM JSON / origin)."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def origin_json() -> Path:
    return FIXTURES / "origin.json"


@pytest.fixture
def dem_tif() -> Path:
    return FIXTURES / "tiny_dem.tif"


@pytest.fixture
def osm_json() -> Path:
    return FIXTURES / "tiny_osm.json"
