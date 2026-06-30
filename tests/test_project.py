"""Projection: lat/lon -> scene-local meters, round-trip and origin handling."""

from __future__ import annotations

from osm2usd.project import Origin, Projector

# The fixture lat/lons were computed to land at these exact local coords for the
# fixture origin (EPSG 25832, SW 480000/5526000).
KNOWN = [
    ((49.88615370, 8.72172774), (10.0, 10.0)),
    ((49.88615470, 8.72214535), (40.0, 10.0)),
    ((49.88642453, 8.72214380), (40.0, 40.0)),
    ((49.88642352, 8.72172619), (10.0, 40.0)),
    ((49.88660374, 8.72186436), (20.0, 60.0)),
]


def test_origin_from_json(origin_json):
    o = Origin.from_json(origin_json)
    assert o.epsg == 25832
    assert o.easting == 480000.0
    assert o.northing == 5526000.0


def test_origin_missing_field(tmp_path):
    import pytest
    bad = tmp_path / "bad.json"
    bad.write_text('{"epsg": 25832}', encoding="utf-8")
    with pytest.raises(ValueError):
        Origin.from_json(bad)


def test_project_known_points(origin_json):
    proj = Projector(Origin.from_json(origin_json))
    for (lat, lon), (ex, ey) in KNOWN:
        x, y = proj.project(lat, lon)
        # ~cm tolerance: pyproj round-trip is exact to well under 1 mm, but the
        # fixture lat/lons were rounded to 8 decimals (~1 mm).
        assert abs(x - ex) < 0.01, f"x {x} != {ex}"
        assert abs(y - ey) < 0.01, f"y {y} != {ey}"


def test_project_many_matches_single(origin_json):
    proj = Projector(Origin.from_json(origin_json))
    latlons = [k[0] for k in KNOWN]
    many = proj.project_many(latlons)
    for (lat, lon), m in zip(latlons, many):
        assert proj.project(lat, lon) == m


def test_project_many_empty(origin_json):
    proj = Projector(Origin.from_json(origin_json))
    assert proj.project_many([]) == []


# --------------------------------------------------------------------------
# Degree-grid mode (Kalahari): linear deg->m remap, no pyproj.
# --------------------------------------------------------------------------

import json

import pytest

from osm2usd.project import (
    DegreeGridProjector,
    MODE_DEGREE_GRID,
    MODE_UTM,
    make_projector,
)

# A synthetic degree-grid origin matching Kalahari's origin.json shape.
# extent W,S,E,N = 20,-26,22,-24  -> 2 deg wide, 2 deg tall.
# width 200000 m, height 300000 m  -> 100000 m/deg lon, 150000 m/deg lat.
_DG = {
    "source_crs": "EPSG:4148",
    "extent_deg": [20.0, -26.0, 22.0, -24.0],
    "sw_lon": 20.0,
    "sw_lat": -26.0,
    "width_m": 200000.0,
    "height_m": 300000.0,
}


def _write_dg(tmp_path):
    p = tmp_path / "kal_origin.json"
    p.write_text(json.dumps(_DG), encoding="utf-8")
    return p


def test_origin_from_json_detects_degree_grid(tmp_path):
    o = Origin.from_json(_write_dg(tmp_path))
    assert o.mode == MODE_DEGREE_GRID
    assert o.sw_lon == 20.0 and o.sw_lat == -26.0
    assert o.extent_w_deg == 2.0 and o.extent_h_deg == 2.0
    assert o.width_m == 200000.0 and o.height_m == 300000.0


def test_degree_grid_sw_corner_is_origin(tmp_path):
    proj = make_projector(Origin.from_json(_write_dg(tmp_path)))
    assert isinstance(proj, DegreeGridProjector)
    x, y = proj.project(-26.0, 20.0)   # SW corner -> (0, 0)
    assert abs(x) < 1e-6 and abs(y) < 1e-6


def test_degree_grid_known_points(tmp_path):
    proj = make_projector(Origin.from_json(_write_dg(tmp_path)))
    # (lat, lon) -> (x, y).  m/deg lon = 100000, m/deg lat = 150000.
    known = [
        ((-26.0, 21.0), (100000.0, 0.0)),       # +1 deg lon
        ((-25.0, 20.0), (0.0, 150000.0)),       # +1 deg lat (north) -> +Y
        ((-24.0, 22.0), (200000.0, 300000.0)),  # NE corner
        ((-26.0, 19.5), (-50000.0, 0.0)),       # WEST of SW corner -> negative X
    ]
    for (lat, lon), (ex, ey) in known:
        x, y = proj.project(lat, lon)
        assert abs(x - ex) < 1e-3, f"x {x} != {ex}"
        assert abs(y - ey) < 1e-3, f"y {y} != {ey}"


def test_degree_grid_y_is_north_up(tmp_path):
    """Higher latitude must give larger Y (north up)."""
    proj = make_projector(Origin.from_json(_write_dg(tmp_path)))
    _, y_south = proj.project(-25.9, 21.0)
    _, y_north = proj.project(-24.1, 21.0)
    assert y_north > y_south


def test_degree_grid_project_many_matches_single(tmp_path):
    proj = make_projector(Origin.from_json(_write_dg(tmp_path)))
    latlons = [(-26.0, 21.0), (-25.0, 20.0), (-24.0, 22.0), (-26.0, 19.5)]
    many = proj.project_many(latlons)
    for (lat, lon), m in zip(latlons, many):
        s = proj.project(lat, lon)
        assert abs(s[0] - m[0]) < 1e-9 and abs(s[1] - m[1]) < 1e-9
    assert proj.project_many([]) == []


def test_origin_from_json_rejects_unknown_shape(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"foo": 1, "bar": 2}', encoding="utf-8")
    with pytest.raises(ValueError):
        Origin.from_json(bad)


def test_make_projector_dispatches_on_mode(tmp_path, origin_json):
    utm = make_projector(Origin.from_json(origin_json))
    dg = make_projector(Origin.from_json(_write_dg(tmp_path)))
    assert type(utm).__name__ == "UtmProjector"
    assert type(dg).__name__ == "DegreeGridProjector"
