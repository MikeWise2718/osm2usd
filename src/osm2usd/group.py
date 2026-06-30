"""Author the toggleable /World/OSM hierarchy.

One merged UsdGeom.Mesh per class group, parented as the viewer's visibility
tab expects::

    /World/OSM
    |-- Roads/{motorway,street,living,service,trail,hwother}
    `-- Buildings/{office,residency,parking,retail,public,farm,school,...}

The bucket list is NOT hardcoded: groups are created for whatever class_group
values appear in the input, slotted under Roads vs Buildings by the way's
`thing` field. Catch-alls (hwother / bldother) arise naturally as just more
class_group values.
"""

from __future__ import annotations

import re

from pxr import Sdf, Usd, UsdGeom, Vt

from .geometry import MeshAccumulator

# "thing" values overpy emits for roads vs buildings. Anything not a road is
# treated as a building (overpy only exports these two thing classes for now).
ROAD_THINGS = {"road", "highway", "path", "way"}

# Per-class ribbon half-widths are derived from these full widths (meters),
# keyed by class_group. Unknown groups fall back to DEFAULT_ROAD_WIDTH.
CLASS_WIDTH: dict[str, float] = {
    "motorway": 12.0,
    "street": 7.0,
    "living": 5.0,
    "service": 4.0,
    "trail": 1.5,
    "footway": 1.5,
    "hwother": 4.0,
}
DEFAULT_ROAD_WIDTH = 4.0


def road_width(class_group: str, scale: float = 1.0) -> float:
    """Ribbon width for a road class, with a global scale multiplier."""
    return CLASS_WIDTH.get(class_group, DEFAULT_ROAD_WIDTH) * scale


def is_road(thing: str) -> bool:
    return (thing or "").lower() in ROAD_THINGS


def _safe_prim_name(name: str) -> str:
    """Make a class_group value a valid USD prim name (alnum + underscore)."""
    s = re.sub(r"[^A-Za-z0-9_]", "_", name or "unknown")
    if not s or s[0].isdigit():
        s = "_" + s
    return s


def define_group_mesh(
    stage: Usd.Stage, parent_path: str, class_group: str, acc: MeshAccumulator
) -> Usd.Prim | None:
    """Author one merged mesh at <parent_path>/<class_group> from an
    accumulator. Returns the mesh prim, or None if the accumulator is empty."""
    if acc.is_empty:
        return None
    name = _safe_prim_name(class_group)
    path = f"{parent_path}/{name}"
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr(
        Vt.Vec3fArray([tuple(map(float, p)) for p in acc.points])
    )
    mesh.CreateFaceVertexCountsAttr(Vt.IntArray(acc.face_vertex_counts))
    mesh.CreateFaceVertexIndicesAttr(Vt.IntArray(acc.face_vertex_indices))
    mesh.CreateSubdivisionSchemeAttr("none")
    mesh.CreateDoubleSidedAttr(True)

    # Extent (bbox) so the viewer can frame/cull the mesh.
    xs = [p[0] for p in acc.points]
    ys = [p[1] for p in acc.points]
    zs = [p[2] for p in acc.points]
    mesh.CreateExtentAttr(
        Vt.Vec3fArray([
            (min(xs), min(ys), min(zs)),
            (max(xs), max(ys), max(zs)),
        ])
    )
    return mesh.GetPrim()


def ensure_scopes(stage: Usd.Stage) -> tuple[str, str, str, str]:
    """Define /World/OSM and the Roads/Buildings/Water scopes. Returns their
    paths (osm_root, roads, buildings, water).

    /World/OSM is an **Xform** (not a Scope) so the viewer can author a single
    height-exaggeration scale op on it, moving the whole overlay to track the
    exaggerated terrain. The per-family children stay Scopes (pure grouping)."""
    UsdGeom.Xform.Define(stage, "/World/OSM")
    roads = "/World/OSM/Roads"
    buildings = "/World/OSM/Buildings"
    water = "/World/OSM/Water"
    UsdGeom.Scope.Define(stage, roads)
    UsdGeom.Scope.Define(stage, buildings)
    UsdGeom.Scope.Define(stage, water)
    return "/World/OSM", roads, buildings, water
