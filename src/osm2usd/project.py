"""Project OSM lat/lon into scene-local meters.

lat/lon (EPSG:4326) -> EPSG:<origin.epsg> (pyproj) -> subtract the SW-corner
(easting0, northing0) -> (x_local, y_local).

This yields the same local-meters, +Y-north, Z-up frame the terrain DEM uses
(messelpit's prep_rasters writes the DEM with from_origin(0, height_m, 1, 1),
crs=None -- SW corner at (0, 0), +Y north). Do NOT use overpy's ll2xz, which is
an untrustworthy OLS fit; this does the real pyproj projection.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pyproj import Transformer

WGS84_EPSG = 4326


@dataclass(frozen=True)
class Origin:
    """Projection origin: the EPSG of the scene CRS plus the SW-corner offset
    (in that CRS's units, i.e. UTM easting/northing meters) that maps the
    scene's SW corner to local (0, 0)."""

    epsg: int
    easting: float
    northing: float

    @classmethod
    def from_json(cls, path: str | Path) -> "Origin":
        """Read an origin.json as produced by messelpit's prep_rasters.

        Expects ``epsg``, ``utm_sw_easting``, ``utm_sw_northing``. The rest of
        the file (dem stats, dims, etc.) is ignored -- osm2usd only needs the
        projection origin and never learns the scene is "Messel".
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        try:
            return cls(
                epsg=int(data["epsg"]),
                easting=float(data["utm_sw_easting"]),
                northing=float(data["utm_sw_northing"]),
            )
        except KeyError as exc:
            raise ValueError(
                f"{path}: origin.json missing required field {exc}"
            ) from exc


class Projector:
    """Projects WGS84 lat/lon to scene-local meters for a given Origin.

    Construct once per build and reuse -- the pyproj Transformer is the
    expensive part to set up.
    """

    def __init__(self, origin: Origin):
        self.origin = origin
        # always_xy=True so transform takes (lon, lat) and returns (easting,
        # northing) -- the GIS x/y convention, avoiding the lat/lon swap trap.
        self._tf = Transformer.from_crs(
            WGS84_EPSG, origin.epsg, always_xy=True
        )

    def project(self, lat: float, lon: float) -> tuple[float, float]:
        """One node: (lat, lon) degrees -> (x_local, y_local) meters."""
        easting, northing = self._tf.transform(lon, lat)
        return easting - self.origin.easting, northing - self.origin.northing

    def project_many(
        self, latlons: list[tuple[float, float]]
    ) -> list[tuple[float, float]]:
        """Vectorized projection of a list of (lat, lon) pairs.

        pyproj transforms arrays far faster than per-point calls, which matters
        for Messel-scale inputs (thousands of ways, tens of thousands of nodes).
        """
        if not latlons:
            return []
        lats = [p[0] for p in latlons]
        lons = [p[1] for p in latlons]
        eastings, northings = self._tf.transform(lons, lats)
        e0, n0 = self.origin.easting, self.origin.northing
        return [(e - e0, n - n0) for e, n in zip(eastings, northings)]
