"""Orchestration: overpy OSM JSON -> draped, class-grouped USD stage.

Pure and scene-agnostic. Every scene-specific input (origin, DEM) is a
parameter; this module never imports a scene repo and never hardcodes Messel.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from pxr import Usd, UsdGeom, Vt

from . import __version__, geometry, group, materials
from .drape import DemSampler, FlatSampler
from .project import MODE_DEGREE_GRID, Origin, make_projector

LEVEL_HEIGHT_DEFAULT = 3.0
DEFAULT_BLD_HEIGHT = 6.0


@dataclass
class BuildOptions:
    default_height: float = DEFAULT_BLD_HEIGHT
    level_height: float = LEVEL_HEIGHT_DEFAULT
    road_width_scale: float = 1.0


@dataclass
class BuildResult:
    out_path: Path
    n_buildings: int = 0
    n_roads: int = 0
    n_skipped: int = 0
    per_class: dict[str, int] = field(default_factory=dict)
    z_min: float = float("inf")
    z_max: float = float("-inf")
    pct_off_dem: float = 0.0
    used_dem: bool = True


def building_height(way: dict, opts: BuildOptions) -> float:
    """Height priority: height_m -> levels x level_height -> default."""
    h = way.get("height_m")
    if h is not None and float(h) > 0:
        return float(h)
    levels = way.get("levels")
    if levels is not None and float(levels) > 0:
        return float(levels) * opts.level_height
    return opts.default_height


def _way_nodes(way: dict) -> list[tuple[float, float]]:
    """Extract [lat, lng] node pairs from a way, tolerant of [lat,lng] lists or
    {lat,lng}/{lat,lon} dicts."""
    out: list[tuple[float, float]] = []
    for node in way.get("nodes", []):
        if isinstance(node, dict):
            lat = node.get("lat")
            lng = node.get("lng", node.get("lon"))
        else:  # [lat, lng]
            lat, lng = node[0], node[1]
        out.append((float(lat), float(lng)))
    return out


def build_stage(
    osm_json: dict,
    origin: Origin,
    sampler: DemSampler | FlatSampler,
    out_path: str | Path,
    opts: BuildOptions,
) -> BuildResult:
    """Build the USD stage from parsed OSM JSON + origin + DEM sampler."""
    out_path = Path(out_path)
    projector = make_projector(origin)
    result = BuildResult(out_path=out_path)
    result.used_dem = isinstance(sampler, DemSampler)

    # Accumulate geometry per (family, class_group). family is "Roads" or
    # "Buildings"; the class_group bucket name comes straight from the data.
    road_acc: dict[str, geometry.MeshAccumulator] = defaultdict(
        geometry.MeshAccumulator
    )
    bld_acc: dict[str, geometry.MeshAccumulator] = defaultdict(
        geometry.MeshAccumulator
    )

    ways = osm_json.get("ways", [])
    for way in ways:
        latlons = _way_nodes(way)
        if len(latlons) < 2:
            result.n_skipped += 1
            continue
        xy = projector.project_many(latlons)
        thing = way.get("thing", "")
        cls = way.get("class_group") or "unknown"

        if group.is_road(thing):
            zs = sampler.sample_many(xy)
            xyz = [(x, y, z) for (x, y), z in zip(xy, zs)]
            width = group.road_width(cls, opts.road_width_scale)
            if geometry.add_road_ribbon(road_acc[cls], xyz, width):
                result.n_roads += 1
                _track_z(result, zs)
            else:
                result.n_skipped += 1
        else:  # building
            if not way.get("closed", True) or len(xy) < 3:
                result.n_skipped += 1
                continue
            base = sampler.base_z(xy)
            height = building_height(way, opts)
            if geometry.add_building(bld_acc[cls], xy, base, height):
                result.n_buildings += 1
                _track_z(result, [base, base + height])
            else:
                result.n_skipped += 1

    _author(out_path, origin, road_acc, bld_acc, result)
    result.pct_off_dem = sampler.stats.pct_clamped
    if result.z_min == float("inf"):
        result.z_min = result.z_max = 0.0
    return result


def _track_z(result: BuildResult, zs: list[float]) -> None:
    for z in zs:
        if z < result.z_min:
            result.z_min = z
        if z > result.z_max:
            result.z_max = z


def _author(out_path, origin, road_acc, bld_acc, result) -> None:
    stage = Usd.Stage.CreateNew(str(out_path))
    stage.SetMetadata("metersPerUnit", 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    stage.SetDefaultPrim(stage.DefinePrim("/World", "Xform"))

    osm_root, roads_path, blds_path = group.ensure_scopes(stage)
    osm_prim = stage.GetPrimAtPath(osm_root)
    osm_prim.SetCustomDataByKey("osm2usd:version", __version__)
    osm_prim.SetCustomDataByKey("osm2usd:origin", _origin_customdata(origin))

    UsdGeom.Scope.Define(stage, "/World/OSM/Materials")
    overlay_paths: list[str] = []

    for family, accs, parent in (
        ("Roads", road_acc, roads_path),
        ("Buildings", bld_acc, blds_path),
    ):
        for cls, acc in sorted(accs.items()):
            prim = group.define_group_mesh(stage, parent, cls, acc)
            if prim is None:
                continue
            mat = materials.define_material(
                stage, "/World/OSM/Materials", cls
            )
            materials.bind(prim, mat)
            n_tris = len(acc.face_vertex_counts)
            result.per_class[f"{family}/{cls}"] = n_tris
            overlay_paths.append(str(prim.GetPath()))

    # Self-describing: list the overlay subtree paths so the viewer can
    # auto-discover the toggleable groups.
    osm_prim.SetCustomDataByKey(
        "osm2usd:overlay_groups", Vt.StringArray(overlay_paths)
    )

    stage.GetRootLayer().Save()


def _origin_customdata(origin: Origin) -> dict:
    """Mode-aware origin record stamped on /World/OSM (only the active mode's
    fields, so a degree-grid build doesn't claim a null epsg/easting)."""
    if origin.mode == MODE_DEGREE_GRID:
        return {
            "mode": origin.mode,
            "sw_lon": origin.sw_lon, "sw_lat": origin.sw_lat,
            "extent_w_deg": origin.extent_w_deg, "extent_h_deg": origin.extent_h_deg,
            "width_m": origin.width_m, "height_m": origin.height_m,
        }
    return {
        "mode": origin.mode,
        "epsg": origin.epsg, "easting": origin.easting, "northing": origin.northing,
    }


def load_osm_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
