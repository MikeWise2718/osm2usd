"""Drape projected vertices onto the DEM by direct array lookup.

The scene DEM is already in the local frame osm2usd projects into: meters, SW
corner at (0, 0), +Y north (messelpit's prep_rasters writes it with
from_origin(0, height_m, res, res), crs=None). messelpit's build_grid_mesh maps
array indices to world coords as::

    x = col * res
    y = (H - 1 - row) * res
    z = dem[row, col]

so the inverse -- finding the DEM cell under a projected (x, y) -- is::

    col = round(x / res)
    row = (H - 1) - round(y / res)

No CRS round-trip is needed at sample time; that is the whole point of the
shared local frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio


@dataclass
class DrapeStats:
    """Bookkeeping for a build's draping, surfaced in the CLI summary."""

    sampled: int = 0
    clamped: int = 0  # vertices whose (col,row) fell outside the DEM bbox

    @property
    def pct_clamped(self) -> float:
        return 100.0 * self.clamped / self.sampled if self.sampled else 0.0


class DemSampler:
    """Local-frame DEM array lookup with edge clamping.

    Vertices outside the DEM bbox are clamped to the nearest edge cell (and
    counted in ``stats.clamped``) rather than dropped, matching the spec's
    leaning resolution for off-DEM nodes. Callers warn loudly if the clamp rate
    is high.
    """

    def __init__(self, dem: np.ndarray, res_m: float, res_y: float | None = None):
        """DEM array in the local frame.

        ``res_m`` is the X (column) pixel size in local meters; ``res_y`` is the
        Y (row) size. For a square grid (Messel, 1 m/px) leave ``res_y`` None and
        it mirrors ``res_m``. For an anisotropic degree grid (Kalahari: ~5.04 m
        in X, ~5.54 m in Y) pass both so the row mapping uses the right pitch.
        """
        if dem.ndim != 2:
            raise ValueError(f"DEM must be 2D, got shape {dem.shape}")
        self.dem = dem
        self.res_m = float(res_m)                 # X pixel size (back-compat name)
        self.res_x = float(res_m)
        self.res_y = float(res_y) if res_y is not None else float(res_m)
        self.H, self.W = dem.shape
        self.stats = DrapeStats()

    @classmethod
    def from_tif(cls, path: str | Path) -> "DemSampler":
        """Open a local-frame dem.tif. Per-axis resolution comes from the
        transform (a = X pixel width, e = Y pixel height; e is negative for a
        north-up raster). prep_rasters writes 1 m/px square for Messel and an
        anisotropic deg-derived pitch for Kalahari; crs is expected to be None."""
        with rasterio.open(path) as src:
            band = src.read(1)
            res_x = abs(src.transform.a)  # X pixel width in local meters
            res_y = abs(src.transform.e)  # Y pixel height in local meters
        return cls(np.asarray(band, dtype=np.float64), res_x, res_y)

    def _cell(self, x: float, y: float) -> tuple[int, int]:
        """(x, y) local meters -> (row, col), clamped to the DEM bounds."""
        col = int(round(x / self.res_x))
        row = (self.H - 1) - int(round(y / self.res_y))
        c = max(0, min(self.W - 1, col))
        r = max(0, min(self.H - 1, row))
        if c != col or r != row:
            self.stats.clamped += 1
        self.stats.sampled += 1
        return r, c

    def sample(self, x: float, y: float) -> float:
        """Draped elevation at a single (x, y) local-meter point."""
        r, c = self._cell(x, y)
        return float(self.dem[r, c])

    def sample_many(self, xys: list[tuple[float, float]]) -> list[float]:
        """Draped z for a list of (x, y) points (per-vertex; used for roads)."""
        return [self.sample(x, y) for x, y in xys]

    def base_z(self, xys: list[tuple[float, float]]) -> float:
        """A single base elevation for a building footprint.

        Uses the MIN of the footprint samples so no corner floats above ground
        on a slope (the spec's leaning choice); the building is then extruded
        flat up from this base.
        """
        if not xys:
            return 0.0
        return min(self.sample(x, y) for x, y in xys)


class FlatSampler:
    """Stand-in used when no DEM is supplied: z = 0 everywhere.

    Debugging only -- Messel's ~60 m relief makes flat placement obviously
    wrong, so build.py warns loudly when this is selected.
    """

    def __init__(self) -> None:
        self.stats = DrapeStats()

    def sample(self, x: float, y: float) -> float:  # noqa: ARG002
        self.stats.sampled += 1
        return 0.0

    def sample_many(self, xys: list[tuple[float, float]]) -> list[float]:
        return [self.sample(x, y) for x, y in xys]

    def base_z(self, xys: list[tuple[float, float]]) -> float:  # noqa: ARG002
        return 0.0
