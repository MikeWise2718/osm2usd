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
