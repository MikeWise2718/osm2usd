"""Geometry: ear-clip triangulation + building/road extrusion."""

from __future__ import annotations

from osm2usd.geometry import (
    MeshAccumulator,
    add_building,
    add_road_ribbon,
    ear_clip,
    signed_area,
)


def test_signed_area_ccw_positive():
    sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert signed_area(sq) > 0


def test_ear_clip_square():
    sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
    tris = ear_clip(sq)
    assert len(tris) == 2  # n-2 triangles for a quad


def test_ear_clip_drops_closing_vertex():
    closed = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    tris = ear_clip(closed)
    assert len(tris) == 2  # closing dup removed, still a quad


def test_ear_clip_pentagon():
    penta = [(0, 0), (4, 0), (5, 3), (2, 5), (-1, 3)]
    assert len(ear_clip(penta)) == 3  # n-2


def test_building_emits_walls_and_cap():
    acc = MeshAccumulator()
    foot = [(10, 10), (40, 10), (40, 40), (10, 40)]
    assert add_building(acc, foot, base_z=100.0, height=12.0)
    # cap: 2 tris; walls: 4 edges x 2 tris = 8; total 10 tris.
    assert len(acc.face_vertex_counts) == 10
    assert all(c == 3 for c in acc.face_vertex_counts)
    # top verts at base+height; bottom at base.
    zs = {round(p[2], 3) for p in acc.points}
    assert zs == {100.0, 112.0}


def test_building_rejects_degenerate():
    acc = MeshAccumulator()
    assert not add_building(acc, [(0, 0), (1, 1)], 0.0, 5.0)
    assert acc.is_empty


def test_road_ribbon_width():
    acc = MeshAccumulator()
    line = [(0, 0, 5.0), (10, 0, 5.0)]  # straight east, z=5
    assert add_road_ribbon(acc, line, width=4.0)
    # one segment -> one quad -> 2 tris.
    assert len(acc.face_vertex_counts) == 2
    # ribbon offset +/- 2 in y (perpendicular to east-west line).
    ys = sorted({round(p[1], 3) for p in acc.points})
    assert ys == [-2.0, 2.0]
    # z preserved from centerline.
    assert {round(p[2], 3) for p in acc.points} == {5.0}


def test_road_ribbon_needs_two_points():
    acc = MeshAccumulator()
    assert not add_road_ribbon(acc, [(0, 0, 0)], 4.0)
