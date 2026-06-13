"""Footprint triangulation (buildings) and ribbon extrusion (roads).

All functions take 2D local-meter coordinates plus draped elevations and emit
flat vertex/index arrays in the (points, faceVertexCounts, faceVertexIndices)
shape USD meshes consume. Geometry is authored as triangles (faceVertexCount
== 3) for RTX/VR safety.
"""

from __future__ import annotations

import math

# A small accumulating mesh: points are (x, y, z) tuples; faces index into them.
Point = tuple[float, float, float]


class MeshAccumulator:
    """Collects points + triangle indices across many ways for one class group,
    so each class group becomes a single merged UsdGeom.Mesh (draw-call sanity
    -- Messel can be thousands of ways)."""

    def __init__(self) -> None:
        self.points: list[Point] = []
        self.face_vertex_counts: list[int] = []
        self.face_vertex_indices: list[int] = []

    def add_triangle(self, a: Point, b: Point, c: Point) -> None:
        base = len(self.points)
        self.points.extend((a, b, c))
        self.face_vertex_counts.append(3)
        self.face_vertex_indices.extend((base, base + 1, base + 2))

    def add_quad(self, a: Point, b: Point, c: Point, d: Point) -> None:
        """Add a quad as two triangles (a,b,c) + (a,c,d)."""
        self.add_triangle(a, b, c)
        self.add_triangle(a, c, d)

    @property
    def is_empty(self) -> bool:
        return not self.points


# --------------------------------------------------------------------------- #
# Polygon helpers (2D, in the XY local-meter plane)
# --------------------------------------------------------------------------- #

def signed_area(ring: list[tuple[float, float]]) -> float:
    """Shoelace signed area. Positive => counter-clockwise winding."""
    n = len(ring)
    s = 0.0
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return 0.5 * s


def _dedup_closing(ring: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Drop a repeated closing vertex (OSM closed ways repeat first==last)."""
    if len(ring) >= 2 and ring[0] == ring[-1]:
        return ring[:-1]
    return ring


def _is_convex(ax, ay, bx, by, cx, cy) -> bool:
    """Is corner B convex, given CCW winding? (left turn => convex)"""
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) > 0


def _point_in_triangle(px, py, ax, ay, bx, by, cx, cy) -> bool:
    d1 = (px - bx) * (ay - by) - (ax - bx) * (py - by)
    d2 = (px - cx) * (by - cy) - (bx - cx) * (py - cy)
    d3 = (px - ax) * (cy - ay) - (cx - ax) * (py - ay)
    has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (has_neg and has_pos)


def ear_clip(ring: list[tuple[float, float]]) -> list[tuple[int, int, int]]:
    """Triangulate a simple polygon by ear clipping.

    Returns triangles as index triples into the (deduped, CCW-normalized) ring.
    Handles the umlaut/relation footprints overpy already resolved into simple
    rings; assumes no self-intersection (OSM building outlines are simple).
    """
    poly = _dedup_closing(list(ring))
    n = len(poly)
    if n < 3:
        return []
    # Normalize to CCW so the convexity test is consistent.
    if signed_area(poly) < 0:
        poly = poly[::-1]

    idx = list(range(len(poly)))
    tris: list[tuple[int, int, int]] = []
    guard = 0
    max_guard = len(poly) * len(poly) + 10
    while len(idx) > 3 and guard < max_guard:
        guard += 1
        ear_found = False
        m = len(idx)
        for i in range(m):
            i0, i1, i2 = idx[(i - 1) % m], idx[i], idx[(i + 1) % m]
            ax, ay = poly[i0]
            bx, by = poly[i1]
            cx, cy = poly[i2]
            if not _is_convex(ax, ay, bx, by, cx, cy):
                continue
            # No other vertex inside the candidate ear.
            contained = False
            for j in idx:
                if j in (i0, i1, i2):
                    continue
                px, py = poly[j]
                if _point_in_triangle(px, py, ax, ay, bx, by, cx, cy):
                    contained = True
                    break
            if contained:
                continue
            tris.append((i0, i1, i2))
            del idx[i]
            ear_found = True
            break
        if not ear_found:
            # Degenerate/collinear remainder: bail with what we have.
            break
    if len(idx) == 3:
        tris.append((idx[0], idx[1], idx[2]))
    return tris


# --------------------------------------------------------------------------- #
# Buildings
# --------------------------------------------------------------------------- #

def add_building(
    acc: MeshAccumulator,
    footprint_xy: list[tuple[float, float]],
    base_z: float,
    height: float,
) -> bool:
    """Extrude a building: walls (per edge) + a flat cap at base_z + height.

    The footprint sits flat at base_z (single draped base). Returns True if any
    geometry was emitted.
    """
    ring = _dedup_closing(list(footprint_xy))
    if len(ring) < 3:
        return False
    top_z = base_z + height

    # Cap (roof), triangulated. Emit with the ring's own winding; both faces are
    # double-sided enough for preview/RTX, and the cap is the visible roof.
    tris = ear_clip(ring)
    for i0, i1, i2 in tris:
        a = (ring[i0][0], ring[i0][1], top_z)
        b = (ring[i1][0], ring[i1][1], top_z)
        c = (ring[i2][0], ring[i2][1], top_z)
        acc.add_triangle(a, b, c)

    # Walls: one quad per footprint edge, base_z -> top_z.
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        bl = (x0, y0, base_z)
        br = (x1, y1, base_z)
        tr = (x1, y1, top_z)
        tl = (x0, y0, top_z)
        acc.add_quad(bl, br, tr, tl)
    return True


# --------------------------------------------------------------------------- #
# Roads
# --------------------------------------------------------------------------- #

def add_road_ribbon(
    acc: MeshAccumulator,
    centerline_xyz: list[tuple[float, float, float]],
    width: float,
) -> bool:
    """Extrude a polyline centerline to a flat ribbon of the given width.

    Each centerline vertex carries its own draped z (roads follow undulation).
    The ribbon is offset +/- width/2 perpendicular to the local segment
    direction in the XY plane; the offset vertices keep the centerline's z so
    the ribbon lies on the terrain. Returns True if any geometry was emitted.
    """
    pts = [p for p in centerline_xyz]
    if len(pts) < 2:
        return False
    half = width / 2.0

    left: list[Point] = []
    right: list[Point] = []
    n = len(pts)
    for i in range(n):
        x, y, z = pts[i]
        # Average the direction of the adjacent segments for a smooth offset.
        if i == 0:
            dx, dy = pts[1][0] - x, pts[1][1] - y
        elif i == n - 1:
            dx, dy = x - pts[i - 1][0], y - pts[i - 1][1]
        else:
            dx0, dy0 = x - pts[i - 1][0], y - pts[i - 1][1]
            dx1, dy1 = pts[i + 1][0] - x, pts[i + 1][1] - y
            dx, dy = dx0 + dx1, dy0 + dy1
        length = math.hypot(dx, dy)
        if length < 1e-9:
            nx, ny = 0.0, 0.0
        else:
            # Perpendicular (left normal) to the direction.
            nx, ny = -dy / length, dx / length
        left.append((x + nx * half, y + ny * half, z))
        right.append((x - nx * half, y - ny * half, z))

    emitted = False
    for i in range(n - 1):
        # Quad spanning segment i: left[i], right[i], right[i+1], left[i+1].
        acc.add_quad(left[i], right[i], right[i + 1], left[i + 1])
        emitted = True
    return emitted
