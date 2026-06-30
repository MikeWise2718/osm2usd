"""Crop projected OSM geometry to the DEM bounds (opt-in, --crop-to-dem).

Overpass returns *complete* ways even when only part of a way lies inside the
query bbox, so long features (4x4 trails, primary roads, boundaries) trail far
past the scene's DEM. Without cropping those tails get edge-clamped by the DEM
sampler, producing high clamp rates and straight streaks to the DEM corners.

This module clips at the projected-XY level (local meters), before geometry:

- **Roads (polylines):** split into the maximal runs that stay inside the DEM
  rectangle, inserting the exact boundary-crossing point where a segment exits
  or enters. A road that leaves and re-enters becomes multiple ribbons (no line
  drawn across the gap). Runs shorter than 2 points are dropped.
- **Buildings (closed footprints):** if any vertex is outside, drop the whole
  footprint. A partially-cropped building outline is meaningless; buildings are
  small, so this is all-or-nothing.

The DEM rectangle is ``[0, max_x] x [0, max_y]`` in local meters (SW corner at
origin), matching the terrain frame.
"""

from __future__ import annotations

XY = tuple[float, float]


def _inside(p: XY, max_x: float, max_y: float) -> bool:
    x, y = p
    return 0.0 <= x <= max_x and 0.0 <= y <= max_y


def _intersect(a: XY, b: XY, max_x: float, max_y: float) -> XY:
    """The point where segment a->b crosses the DEM rectangle boundary.

    Exactly one of a/b is inside (callers guarantee this). Returns the crossing
    point on the rectangle edge by clipping the parametric segment to the
    [0,max] box on each axis (Liang-Barsky style, reduced to the single
    enter/exit crossing we need).
    """
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay

    # Parametric t in [0,1] along a->b; find the tightest box crossing.
    t_lo, t_hi = 0.0, 1.0
    for p, q in (
        (-dx, ax - 0.0),       # x >= 0
        (dx, max_x - ax),      # x <= max_x
        (-dy, ay - 0.0),       # y >= 0
        (dy, max_y - ay),      # y <= max_y
    ):
        if p == 0:
            # Parallel to this edge; outside if q < 0 (handled by caller's inside test).
            continue
        t = q / p
        if p < 0:
            t_lo = max(t_lo, t)
        else:
            t_hi = min(t_hi, t)

    # The segment has one endpoint in, one out, so [t_lo, t_hi] brackets the
    # in-box portion; the crossing toward the outside endpoint is the relevant t.
    t = t_hi if _inside(a, max_x, max_y) else t_lo
    t = max(0.0, min(1.0, t))
    return (ax + dx * t, ay + dy * t)


def crop_polyline(
    pts: list[XY], max_x: float, max_y: float
) -> list[list[XY]]:
    """Split a polyline into the maximal in-DEM runs, clipping at the boundary.

    Returns a list of sub-polylines (each >= 2 points) that lie inside the DEM
    rectangle. Boundary-crossing points are interpolated so each run ends/starts
    exactly on the edge. An entirely-inside polyline returns ``[pts]``; an
    entirely-outside one returns ``[]``.
    """
    if len(pts) < 2:
        return []
    runs: list[list[XY]] = []
    cur: list[XY] = []
    prev = pts[0]
    prev_in = _inside(prev, max_x, max_y)
    if prev_in:
        cur.append(prev)

    for p in pts[1:]:
        p_in = _inside(p, max_x, max_y)
        if prev_in and p_in:
            cur.append(p)
        elif prev_in and not p_in:
            # Exiting: end the run on the boundary crossing.
            cur.append(_intersect(prev, p, max_x, max_y))
            if len(cur) >= 2:
                runs.append(cur)
            cur = []
        elif not prev_in and p_in:
            # Entering: start a new run at the boundary crossing.
            cur = [_intersect(prev, p, max_x, max_y), p]
        # else: both outside -> nothing (we don't try to recover a segment that
        # passes through the box without a vertex inside; rare at OSM density).
        prev, prev_in = p, p_in

    if len(cur) >= 2:
        runs.append(cur)
    return runs


def building_inside(footprint: list[XY], max_x: float, max_y: float) -> bool:
    """True if every footprint vertex is inside the DEM rectangle (else drop)."""
    return all(_inside(p, max_x, max_y) for p in footprint)
