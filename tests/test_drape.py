"""Drape: DEM array lookup in the local frame + clamping."""

from __future__ import annotations

import numpy as np

from osm2usd.drape import DemSampler, FlatSampler


def test_dem_loads(dem_tif):
    s = DemSampler.from_tif(dem_tif)
    assert s.res_m == 1.0
    assert (s.H, s.W) == (100, 100)


def test_sample_matches_formula(dem_tif):
    """The fixture DEM is z = 100 + x*0.1, constant in y. Draping at (x,y) must
    return that exact value (this is the local-frame mapping contract)."""
    s = DemSampler.from_tif(dem_tif)
    # tolerance is float32 storage precision, not the lookup logic.
    for x, y in [(10, 10), (40, 40), (10, 40), (20, 60), (0, 0), (99, 99)]:
        assert abs(s.sample(x, y) - (100.0 + x * 0.1)) < 1e-4


def test_y_axis_is_north_up(dem_tif):
    """Because the DEM is constant in y, varying y must not change z -- proving
    the row = (H-1) - round(y/res) inversion lands in the right row band."""
    s = DemSampler.from_tif(dem_tif)
    zs = {round(s.sample(50, y), 4) for y in range(0, 100, 10)}
    assert len(zs) == 1


def test_base_z_uses_min(dem_tif):
    s = DemSampler.from_tif(dem_tif)
    # footprint spanning x in [10,40] -> min z at x=10 => 101.0
    foot = [(10, 10), (40, 10), (40, 40), (10, 40)]
    assert abs(s.base_z(foot) - 101.0) < 1e-6


def test_clamp_counts_off_dem():
    dem = np.zeros((10, 10), dtype=np.float64)
    s = DemSampler(dem, 1.0)
    s.sample(5, 5)       # inside
    s.sample(-100, -100)  # outside -> clamped
    s.sample(1000, 1000)  # outside -> clamped
    assert s.stats.sampled == 3
    assert s.stats.clamped == 2
    assert abs(s.stats.pct_clamped - (200.0 / 3)) < 1e-6


def test_flat_sampler():
    f = FlatSampler()
    assert f.sample(5, 5) == 0.0
    assert f.base_z([(1, 1), (2, 2)]) == 0.0
    assert f.sample_many([(0, 0), (1, 1)]) == [0.0, 0.0]


def test_anisotropic_res_maps_correct_cell():
    """Degree-grid DEMs have res_x != res_y. The row mapping must use res_y and
    the col mapping res_x, or features creep along the mismatched axis."""
    # 10x10 DEM; encode the row index into z so we can detect which row was hit.
    dem = np.zeros((10, 10), dtype=np.float64)
    for r in range(10):
        dem[r, :] = float(r)
    res_x, res_y = 4.0, 7.0
    s = DemSampler(dem, res_x, res_y)
    assert s.res_x == 4.0 and s.res_y == 7.0
    # y = 0 -> bottom row = row 9 (H-1) -> z == 9.
    assert s.sample(0.0, 0.0) == 9.0
    # y = 2*res_y = 14 -> row = 9 - round(14/7) = 9 - 2 = 7 -> z == 7.
    assert s.sample(0.0, 14.0) == 7.0
    # x uses res_x: x = 3*res_x = 12 -> col 3 (z constant across cols, still row-based).
    assert s.sample(12.0, 0.0) == 9.0


def test_isotropic_back_compat():
    """A single res still works (res_y mirrors res_x) -- the Messel path."""
    dem = np.zeros((10, 10), dtype=np.float64)
    s = DemSampler(dem, 1.0)
    assert s.res_x == 1.0 and s.res_y == 1.0 and s.res_m == 1.0
