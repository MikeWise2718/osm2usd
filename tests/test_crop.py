"""Crop projected geometry to the DEM bbox (--crop-to-dem)."""

from __future__ import annotations

from osm2usd.crop import building_inside, crop_polyline

MAXX, MAXY = 100.0, 100.0


def test_fully_inside_polyline_unchanged():
    pts = [(10, 10), (50, 50), (90, 20)]
    runs = crop_polyline(pts, MAXX, MAXY)
    assert runs == [pts]


def test_fully_outside_polyline_dropped():
    pts = [(-50, -50), (-10, -20), (-5, 200)]
    assert crop_polyline(pts, MAXX, MAXY) == []


def test_exit_clips_at_boundary():
    # Starts inside, exits the right edge (x=100) between (90,50) and (110,50).
    pts = [(90, 50), (110, 50)]
    runs = crop_polyline(pts, MAXX, MAXY)
    assert len(runs) == 1
    run = runs[0]
    assert run[0] == (90, 50)
    # crossing at x=100, y=50
    assert abs(run[-1][0] - 100.0) < 1e-9
    assert abs(run[-1][1] - 50.0) < 1e-9


def test_enter_clips_at_boundary():
    # Starts outside (x<0), enters; run begins at x=0.
    pts = [(-20, 50), (40, 50)]
    runs = crop_polyline(pts, MAXX, MAXY)
    assert len(runs) == 1
    assert abs(runs[0][0][0] - 0.0) < 1e-9
    assert runs[0][-1] == (40, 50)


def test_exit_and_reenter_splits_into_two_runs():
    # in -> out (right) -> ... -> in again: two separate runs, no line across.
    pts = [(50, 50), (150, 50), (150, 10), (50, 10)]
    runs = crop_polyline(pts, MAXX, MAXY)
    assert len(runs) == 2
    # each run stays within the box
    for run in runs:
        for x, y in run:
            assert -1e-6 <= x <= MAXX + 1e-6
            assert -1e-6 <= y <= MAXY + 1e-6


def test_single_inside_point_run_dropped():
    # A lone in-box vertex between two out-of-box ones can't form a >=2 run.
    pts = [(-10, 50), (50, 200)]  # first out, second out (y>max) -> nothing
    assert crop_polyline(pts, MAXX, MAXY) == []


def test_building_inside():
    assert building_inside([(10, 10), (90, 10), (90, 90), (10, 90)], MAXX, MAXY)
    assert not building_inside([(10, 10), (110, 10), (90, 90)], MAXX, MAXY)
    assert not building_inside([(-1, 10), (50, 50)], MAXX, MAXY)
