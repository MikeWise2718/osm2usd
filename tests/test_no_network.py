"""Guard: the build path must never touch the network.

osm2usd works purely from local files (JSON, origin.json, dem.tif). This test
monkeypatches socket creation to fail, then runs a full build, ensuring no
module reaches out (e.g. pyproj must use bundled PROJ data, not a network grid).
"""

from __future__ import annotations

import socket

import pytest

from osm2usd.build import BuildOptions, build_stage, load_osm_json
from osm2usd.drape import DemSampler
from osm2usd.project import Origin


@pytest.fixture
def no_socket(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("network access attempted during build")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def test_build_is_offline(no_socket, osm_json, origin_json, dem_tif, tmp_path):
    out = tmp_path / "offline_osm.usd"
    res = build_stage(
        load_osm_json(osm_json),
        Origin.from_json(origin_json),
        DemSampler.from_tif(dem_tif),
        out,
        BuildOptions(),
    )
    assert out.exists()
    assert res.n_buildings + res.n_roads > 0
