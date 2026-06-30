"""Orchestration: overpy OSM JSON -> draped, class-grouped USD stage.

Pure and scene-agnostic. Every scene-specific input (origin, DEM) is a
parameter; this module never imports a scene repo and never hardcodes Messel.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from pxr import Usd, UsdGeom, Vt

from . import __version__, crop, geometry, group, materials
from .drape import DemSampler, FlatSampler
from .project import MODE_DEGREE_GRID, Origin, make_projector

LEVEL_HEIGHT_DEFAULT = 3.0
DEFAULT_BLD_HEIGHT = 6.0


MARKER_RADIUS_DEFAULT = 300.0    # meters; oversized so points show at scene scale
MARKER_HEIGHT_DEFAULT = 1500.0   # a slim, tall pin reads better than a fat block


@dataclass
class BuildOptions:
    default_height: float = DEFAULT_BLD_HEIGHT
    level_height: float = LEVEL_HEIGHT_DEFAULT
    road_width_scale: float = 1.0
    crop_to_dem: bool = False  # split roads / drop buildings outside the DEM bbox
    marker_radius: float = MARKER_RADIUS_DEFAULT  # point-marker footprint radius (m)
    marker_height: float = MARKER_HEIGHT_DEFAULT  # point-marker pillar height (m)


@dataclass
class NamedWay:
    """A real-named road/building authored as its OWN named prim (pickable, so a
    click shows its name), instead of being merged into the per-class mesh.
    `family` is 'Roads' or 'Buildings'; base_z is the draped ground z (markers
    need it; ways carry it for symmetry / future re-basing)."""
    family: str
    cls: str
    name: str
    idx: int
    acc: "geometry.MeshAccumulator"
    base_z: float


@dataclass
class PointMarker:
    """One point rendered as its own named prim (not merged), so it's
    individually pickable and re-baseable. `base_z` is the draped ground z at the
    marker (the viewer needs it to keep the pin on the exaggerated terrain)."""
    cls: str
    name: str
    idx: int
    acc: "geometry.MeshAccumulator"
    base_z: float


@dataclass
class BuildResult:
    out_path: Path
    n_buildings: int = 0
    n_roads: int = 0
    n_points: int = 0    # point markers emitted (e.g. waterholes)
    n_skipped: int = 0
    n_cropped: int = 0   # ways/points dropped entirely as outside the DEM (crop_to_dem)
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


# overpy emits a `name` for every way, but most are synthetic placeholders it
# generates when OSM had none: a class-word (or "yes"/"house"/...) + digits, e.g.
# "track001", "service002", "unclassified010", "yes007", "apartments003". A real
# name ("Nossob Road", "Rooiputs Camp Site No. 1") is anything that ISN'T that
# pattern. Only real-named ways get split into their own pickable prim; the rest
# stay in the merged per-class mesh (draw-call sanity).
_SYNTHETIC_NAME = re.compile(
    r"^(track|service|unclassified|primary|secondary|tertiary|residential|"
    r"living|footway|path|cycleway|pedestrian|step|yes|building|house|roof|"
    r"apartments|appartments|terrace|carport|garage|garages|hangar|"
    r"water_storage|manufacture|hwother|bldother|commercial)\d*$",
    re.IGNORECASE,
)


def real_name(way: dict) -> str | None:
    """The way's genuine OSM name, or None if absent/synthetic placeholder."""
    n = (way.get("name") or "").strip()
    if not n or _SYNTHETIC_NAME.match(n):
        return None
    return n


def _point_class(pt: dict) -> str:
    """Class-group bucket for an overpy point. Boreholes/wells/springs/water
    points all bucket to 'waterhole' (blue marker, /World/OSM/Water/waterhole);
    anything else falls back to a generic 'point'."""
    kind = (pt.get("kind") or "").lower()
    water_kinds = {
        "borehole", "well", "water_well", "spring", "waterhole",
        "drinking_water", "water_point", "pond", "dam",
    }
    return "waterhole" if kind in water_kinds else "point"


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

    # Accumulate geometry per (family, class_group). family is "Roads",
    # "Buildings", or "Water"; the class_group bucket name comes from the data.
    road_acc: dict[str, geometry.MeshAccumulator] = defaultdict(
        geometry.MeshAccumulator
    )
    bld_acc: dict[str, geometry.MeshAccumulator] = defaultdict(
        geometry.MeshAccumulator
    )

    # When cropping, clip to the DEM rectangle in local meters (only meaningful
    # with a real DEM; FlatSampler has no bounds).
    crop_box = None
    if opts.crop_to_dem and isinstance(sampler, DemSampler):
        crop_box = sampler.max_xy

    # Real-named ways are collected as their own pickable prims; unnamed ways
    # are merged into the per-class meshes above.
    named_ways: list[NamedWay] = []

    ways = osm_json.get("ways", [])
    for idx, way in enumerate(ways):
        latlons = _way_nodes(way)
        if len(latlons) < 2:
            result.n_skipped += 1
            continue
        xy = projector.project_many(latlons)
        thing = way.get("thing", "")
        cls = way.get("class_group") or "unknown"
        nm = real_name(way)

        if group.is_road(thing):
            width = group.road_width(cls, opts.road_width_scale)
            # Crop a road into the in-DEM runs (each becomes its own ribbon) so
            # tails outside the scene aren't drawn as streaks to the edge.
            runs = (
                crop.crop_polyline(xy, crop_box[0], crop_box[1])
                if crop_box is not None
                else [xy]
            )
            if not runs:
                result.n_cropped += 1
                continue
            # Named road -> its own accumulator (own prim); else the class mesh.
            own = geometry.MeshAccumulator() if nm else None
            target = own if own is not None else road_acc[cls]
            emitted_any = False
            base_z = None
            for run in runs:
                zs = sampler.sample_many(run)
                xyz = [(x, y, z) for (x, y), z in zip(run, zs)]
                if geometry.add_road_ribbon(target, xyz, width):
                    emitted_any = True
                    _track_z(result, zs)
                    if base_z is None and zs:
                        base_z = min(zs)
            if emitted_any:
                result.n_roads += 1
                if own is not None:
                    named_ways.append(NamedWay(
                        family="Roads", cls=cls, name=nm, idx=idx,
                        acc=own, base_z=base_z or 0.0))
            else:
                result.n_skipped += 1
        else:  # building
            if not way.get("closed", True) or len(xy) < 3:
                result.n_skipped += 1
                continue
            # Drop buildings that aren't fully inside the DEM (a partial
            # footprint is meaningless; buildings are small).
            if crop_box is not None and not crop.building_inside(
                xy, crop_box[0], crop_box[1]
            ):
                result.n_cropped += 1
                continue
            base = sampler.base_z(xy)
            height = building_height(way, opts)
            own = geometry.MeshAccumulator() if nm else None
            target = own if own is not None else bld_acc[cls]
            if geometry.add_building(target, xy, base, height):
                result.n_buildings += 1
                _track_z(result, [base, base + height])
                if own is not None:
                    named_ways.append(NamedWay(
                        family="Buildings", cls=cls, name=nm, idx=idx,
                        acc=own, base_z=base))
            else:
                result.n_skipped += 1

    # Points -> draped markers (e.g. waterholes/boreholes). overpy points carry
    # {name, kind, lat, lng}; class group derives from `kind` (borehole/well/
    # spring/... -> "waterhole"). Markers are oversized navigation pins.
    #
    # Each point becomes its OWN named prim (not merged) so it is individually
    # pickable (hover/click -> name tooltips) and the viewer can re-base each
    # marker onto the exaggerated terrain (a point has no real height, so it must
    # not stretch). 49 markers is cheap to keep separate; ways stay merged.
    markers: list[PointMarker] = []
    for idx, pt in enumerate(osm_json.get("points", [])):
        try:
            lat = float(pt["lat"])
            lon = float(pt.get("lng", pt.get("lon")))
        except (KeyError, TypeError, ValueError):
            result.n_skipped += 1
            continue
        x, y = projector.project(lat, lon)
        if crop_box is not None and not crop._inside((x, y), crop_box[0], crop_box[1]):
            result.n_cropped += 1
            continue
        cls = _point_class(pt)
        base = sampler.sample(x, y)
        acc = geometry.MeshAccumulator()
        if geometry.add_marker(
            acc, x, y, base, opts.marker_radius, opts.marker_height
        ):
            markers.append(PointMarker(
                cls=cls, name=str(pt.get("name") or ""), idx=idx,
                acc=acc, base_z=base,
            ))
            result.n_points += 1
            _track_z(result, [base, base + opts.marker_height])

    _author(out_path, origin, road_acc, bld_acc, markers, named_ways, result)
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


def _author(out_path, origin, road_acc, bld_acc, markers, named_ways, result) -> None:
    stage = Usd.Stage.CreateNew(str(out_path))
    stage.SetMetadata("metersPerUnit", 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    stage.SetDefaultPrim(stage.DefinePrim("/World", "Xform"))

    osm_root, roads_path, blds_path, water_path = group.ensure_scopes(stage)
    osm_prim = stage.GetPrimAtPath(osm_root)
    osm_prim.SetCustomDataByKey("osm2usd:version", __version__)
    osm_prim.SetCustomDataByKey("osm2usd:origin", _origin_customdata(origin))

    UsdGeom.Scope.Define(stage, "/World/OSM/Materials")
    overlay_paths: list[str] = []

    def _mat(cls):
        return materials.define_material(stage, "/World/OSM/Materials", cls)

    # Named ways grouped by (family, class) so a class with named features can
    # host them as children alongside the merged-remainder mesh.
    named_by = defaultdict(list)
    for nw in named_ways:
        named_by[(nw.family, nw.cls)].append(nw)

    # Roads + buildings: unnamed features merge into one mesh per class; real-
    # named features get their own pickable child prim. A class that has named
    # features becomes a Scope holding <cls>/_merged (remainder) + <cls>/<name>;
    # a class with none stays a flat <cls> Mesh (back-compat). Either way the
    # visibility toggle path /World/OSM/<family>/<cls> still hides everything
    # under it via ancestor invisibility.
    for family, accs, parent in (
        ("Roads", road_acc, roads_path),
        ("Buildings", bld_acc, blds_path),
    ):
        classes = set(accs) | {c for (f, c) in named_by if f == family}
        for cls in sorted(classes):
            acc = accs.get(cls)
            nws = named_by.get((family, cls), [])
            tris = 0
            if nws:
                cls_scope = f"{parent}/{group._safe_prim_name(cls)}"
                UsdGeom.Scope.Define(stage, cls_scope)
                # merged remainder (unnamed features of this class)
                if acc is not None and not acc.is_empty:
                    mprim = group.define_named_mesh(stage, cls_scope, "_merged", acc)
                    if mprim is not None:
                        materials.bind(mprim, _mat(cls))
                        tris += len(acc.face_vertex_counts)
                # named features as pickable children
                seen: set = set()
                for nw in nws:
                    leaf = group.unique_prim_name(nw.name, nw.idx, seen)
                    p = group.define_named_mesh(stage, cls_scope, leaf, nw.acc)
                    if p is None:
                        continue
                    p.SetCustomDataByKey("osm2usd:name", nw.name)
                    p.SetCustomDataByKey("osm2usd:base_z", float(nw.base_z))
                    materials.bind(p, _mat(cls))
                    tris += len(nw.acc.face_vertex_counts)
                overlay_paths.append(cls_scope)
            else:
                prim = group.define_group_mesh(stage, parent, cls, acc)
                if prim is None:
                    continue
                materials.bind(prim, _mat(cls))
                tris = len(acc.face_vertex_counts)
                overlay_paths.append(str(prim.GetPath()))
            result.per_class[f"{family}/{cls}"] = tris

    # Water: one NAMED prim per point under /World/OSM/Water/<class>/<safe_name>,
    # so each is individually pickable (name tooltips) and re-baseable (the viewer
    # keeps the pin on the exaggerated terrain via per-marker base_z). The class
    # group prim stays a Scope so the visibility tab's /World/OSM/Water/<class>
    # toggle hides them all via ancestor invisibility.
    used_names: dict[str, set] = {}
    for mk in markers:
        cls_scope = f"{water_path}/{mk.cls}"
        UsdGeom.Scope.Define(stage, cls_scope)
        seen = used_names.setdefault(mk.cls, set())
        leaf = group.unique_prim_name(mk.name, mk.idx, seen)
        prim = group.define_named_mesh(stage, cls_scope, leaf, mk.acc)
        if prim is None:
            continue
        # Per-marker metadata: the display name + the draped ground z (the viewer
        # re-bases the pin onto exaggerated terrain using base_z).
        if mk.name:
            prim.SetCustomDataByKey("osm2usd:name", mk.name)
        prim.SetCustomDataByKey("osm2usd:base_z", float(mk.base_z))
        materials.bind(prim, materials.define_material(
            stage, "/World/OSM/Materials", mk.cls))
        result.per_class[f"Water/{mk.cls}"] = (
            result.per_class.get(f"Water/{mk.cls}", 0) + len(mk.acc.face_vertex_counts)
        )
    # The toggle target is the class scope, not each marker (keep overlay_groups tidy).
    for cls in sorted(used_names):
        overlay_paths.append(f"{water_path}/{cls}")

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
