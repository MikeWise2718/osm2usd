"""Project OSM lat/lon into scene-local meters.

Two scene frames are supported, because the terrain pipelines differ:

1. **UTM / metric** (Messel): the DEM is built in a projected metric CRS. We
   project lat/lon (EPSG:4326) -> EPSG:<origin.epsg> via pyproj, then subtract
   the SW-corner (easting0, northing0):

       lat/lon -> pyproj(epsg) -> (e - e0, n - n0)

2. **Degree grid** (Kalahari): the DEM is NOT in a metric projection -- it's a
   geographic (lat/lon) raster whose local meters come from *anisotropic*
   per-axis scaling of the extent, exactly as Kalihari_dt's build_grid_mesh
   authors the mesh (px = col*res_x, py = (H-1-row)*res_y, +Y north). So local
   meters are a linear remap of degrees within the extent box:

       x = (lon - sw_lon) / extent_w_deg * width_m
       y = (lat - sw_lat) / extent_h_deg * height_m     (+Y north; lat up = north)

Both yield the same local-meters, +Y-north, Z-up frame the terrain DEM uses, so
everything downstream (drape, geometry, grouping) is frame-agnostic.

`Origin.from_json` auto-detects the mode from which keys the origin.json carries
(messelpit's prep_rasters writes `epsg`/`utm_sw_easting`; Kalihari_dt's writes
`sw_lon`/`extent_deg`/`width_m`). Do NOT use overpy's ll2xz (an untrustworthy
OLS fit); the UTM path does the real pyproj projection and the degree path does
the exact same linear remap the terrain mesh used.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

WGS84_EPSG = 4326

# Origin modes.
MODE_UTM = "utm"
MODE_DEGREE_GRID = "degree_grid"


@dataclass(frozen=True)
class Origin:
    """Projection origin for a scene.

    Polymorphic by ``mode``:

    - ``utm``: needs ``epsg`` + ``easting``/``northing`` (the SW-corner offset
      in the projected CRS's metric units).
    - ``degree_grid``: needs ``sw_lon``/``sw_lat`` + ``extent_w_deg``/
      ``extent_h_deg`` + ``width_m``/``height_m`` (the linear deg->m remap).

    Only the fields for the active mode are populated; the others stay None.
    """

    mode: str = MODE_UTM
    # --- utm ---
    epsg: int | None = None
    easting: float | None = None
    northing: float | None = None
    # --- degree_grid ---
    sw_lon: float | None = None
    sw_lat: float | None = None
    extent_w_deg: float | None = None
    extent_h_deg: float | None = None
    width_m: float | None = None
    height_m: float | None = None

    @classmethod
    def from_json(cls, path: str | Path) -> "Origin":
        """Read an origin.json, auto-detecting the scene frame.

        - **UTM** (messelpit prep_rasters): ``epsg`` + ``utm_sw_easting`` +
          ``utm_sw_northing``.
        - **Degree grid** (Kalihari_dt prep_rasters): ``sw_lon``/``sw_lat``
          (or derivable from ``extent_deg``) + ``extent_deg`` + ``width_m`` +
          ``height_m``.

        Raises ValueError with a clear message if neither shape is present.
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))

        # UTM: presence of the metric SW corner is the signal.
        if "utm_sw_easting" in data and "utm_sw_northing" in data and "epsg" in data:
            return cls(
                mode=MODE_UTM,
                epsg=int(data["epsg"]),
                easting=float(data["utm_sw_easting"]),
                northing=float(data["utm_sw_northing"]),
            )

        # Degree grid: an extent box + physical size in meters.
        if "extent_deg" in data and "width_m" in data and "height_m" in data:
            w, s, e, n = (float(v) for v in data["extent_deg"])  # W,S,E,N
            sw_lon = float(data.get("sw_lon", w))
            sw_lat = float(data.get("sw_lat", s))
            return cls(
                mode=MODE_DEGREE_GRID,
                sw_lon=sw_lon,
                sw_lat=sw_lat,
                extent_w_deg=e - w,
                extent_h_deg=n - s,
                width_m=float(data["width_m"]),
                height_m=float(data["height_m"]),
            )

        raise ValueError(
            f"{path}: origin.json is neither a UTM frame "
            f"(epsg + utm_sw_easting + utm_sw_northing) nor a degree grid "
            f"(extent_deg + width_m + height_m)."
        )


class UtmProjector:
    """Project WGS84 lat/lon to scene-local meters via pyproj to a metric CRS.

    Construct once per build and reuse -- the pyproj Transformer is the
    expensive part to set up. (pyproj is imported lazily so the degree-grid
    path has no hard pyproj dependency.)
    """

    def __init__(self, origin: Origin):
        if origin.mode != MODE_UTM:
            raise ValueError(f"UtmProjector needs a UTM origin, got {origin.mode}")
        from pyproj import Transformer

        self.origin = origin
        # always_xy=True so transform takes (lon, lat) and returns (easting,
        # northing) -- the GIS x/y convention, avoiding the lat/lon swap trap.
        self._tf = Transformer.from_crs(WGS84_EPSG, origin.epsg, always_xy=True)

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


class DegreeGridProjector:
    """Project WGS84 lat/lon to scene-local meters by linear deg->m remap.

    No pyproj: the scene's terrain is a geographic raster whose local frame is a
    straight rescale of the extent box (the exact inverse of build_grid_mesh's
    px = col*res_x, py = (H-1-row)*res_y mapping). +Y is north because latitude
    increases northward and the SW corner is local (0, 0).
    """

    def __init__(self, origin: Origin):
        if origin.mode != MODE_DEGREE_GRID:
            raise ValueError(
                f"DegreeGridProjector needs a degree_grid origin, got {origin.mode}"
            )
        self.origin = origin
        self._sx = origin.width_m / origin.extent_w_deg   # meters per degree lon
        self._sy = origin.height_m / origin.extent_h_deg  # meters per degree lat

    def project(self, lat: float, lon: float) -> tuple[float, float]:
        x = (lon - self.origin.sw_lon) * self._sx
        y = (lat - self.origin.sw_lat) * self._sy   # lat up -> +Y north
        return x, y

    def project_many(
        self, latlons: list[tuple[float, float]]
    ) -> list[tuple[float, float]]:
        if not latlons:
            return []
        o = self.origin
        return [
            ((lon - o.sw_lon) * self._sx, (lat - o.sw_lat) * self._sy)
            for lat, lon in latlons
        ]


def make_projector(origin: Origin):
    """Return the projector matching the origin's mode (UTM or degree grid)."""
    if origin.mode == MODE_DEGREE_GRID:
        return DegreeGridProjector(origin)
    return UtmProjector(origin)


# Back-compat alias: existing callers/tests construct `Projector(origin)` for the
# UTM path. Keep it pointing at the UTM projector so nothing downstream changes.
Projector = UtmProjector
