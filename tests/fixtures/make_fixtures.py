"""Regenerate the committed test fixtures: a tiny synthetic DEM + origin.json.

The OSM JSON (tiny_osm.json) is hand-authored and committed directly; its
lat/lon values were computed to land at known local-meter positions for the
origin below, so tests can assert hand-computed coordinates.

Run from the repo root with the project venv::

    python tests/fixtures/make_fixtures.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

HERE = Path(__file__).parent

# Origin near Messel; deliberately simple round numbers.
EPSG = 25832
SW_EASTING = 480000.0
SW_NORTHING = 5526000.0

# A 100x100 m DEM at 1 m/px. z = 100 + x*0.1 (a gentle east-facing slope), so
# the draped z at a known (x, y) is exactly predictable: z = 100 + x*0.1.
RES = 1.0
W = H = 100


def make_dem() -> np.ndarray:
    cols = np.arange(W, dtype=np.float64)  # col == x in meters (res=1)
    z_row = 100.0 + cols * 0.1
    dem = np.tile(z_row, (H, 1)).astype("float32")  # constant in y
    return dem


def write_dem(dem: np.ndarray) -> None:
    height_m = H * RES
    transform = from_origin(0.0, height_m, RES, RES)  # SW at (0,0), +Y north
    profile = dict(
        driver="GTiff", height=H, width=W, count=1, dtype="float32",
        transform=transform, crs=None, compress="lzw",
    )
    with rasterio.open(HERE / "tiny_dem.tif", "w", **profile) as dst:
        dst.write(dem, 1)


def write_origin() -> None:
    (HERE / "origin.json").write_text(
        json.dumps(
            {
                "epsg": EPSG,
                "utm_sw_easting": SW_EASTING,
                "utm_sw_northing": SW_NORTHING,
                "width_m": W * RES,
                "height_m": H * RES,
                "dem_resolution_m": RES,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    dem = make_dem()
    write_dem(dem)
    write_origin()
    print(f"wrote tiny_dem.tif ({W}x{H}) and origin.json to {HERE}")
