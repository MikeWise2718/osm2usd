# CLAUDE.md — osm2usd

Project-specific guidance for Claude sessions working in this repo. Read this
alongside the global `~/.claude/CLAUDE.md` and the full spec in
[`specs/osm2usd.md`](specs/osm2usd.md).

## What this project is

A **scene-agnostic** Python package that turns overpy's OSM JSON into draped,
class-grouped USD: project lat/lon → scene-local meters, extrude building
footprints and road ribbons, drape them onto a DEM, and group everything under
a toggleable `/World/OSM` hierarchy.

It is the generic builder in a deliberate split (the same one already validated
by `usd_viewer` vs `messelpit_viewer`):

| Repo | Role |
|---|---|
| **`osm2usd`** (this repo) | Scene-agnostic builder: JSON + origin + DEM → grouped USD |
| **`messelpit`** | Owns Messel DEM/origin; thin call into osm2usd + composition |
| **`kalahari`** (future) | Owns Kalahari DEM/origin; same thin call |

## The one invariant — never import a scene

**osm2usd is pure. Every scene-specific input is a parameter.** It must never:

- `import messelpit` (or any scene repo),
- hardcode an origin (EPSG / easting / northing),
- assume Messel, or special-case any single site.

The projection origin, DEM, and OSM JSON all arrive as CLI args / function
parameters. If you find yourself reaching for a Messel-specific constant, that
logic belongs in the **scene repo**, not here. This is the whole reason the
package exists separately — putting reusable logic in a single-scene repo would
force Kalahari to depend on a *Messel* repo or copy-paste and drift.

## The contract this sits between

```
overpy ──<area>_osm.json──> osm2usd ──<scene>_osm.usd──> messelpit composition
                               ▲
                       origin.json + dem.tif   (supplied by the scene repo)
```

## Coordinate frame (must match the terrain exactly)

The scene's DEM is already in **local meters, SW corner at (0,0), +Y north,
Z up** — messelpit's `prep_rasters.py` writes it with
`from_origin(0.0, height_m, 1.0, 1.0)`, `crs=None`. So draping is a direct
array lookup, no CRS round-trip at sample time:

- project: `lat/lon (EPSG:4326) → EPSG:<origin.epsg> (pyproj) → subtract (easting0, northing0)`
- drape: `col = round(x/res)`, `row = (H-1) - round(y/res)`, `z = dem[row, col]`

Match the terrain stage conventions: `metersPerUnit = 1.0`, `UpAxis = Z`,
default prim `/World`.

**Do not** use overpy's `ll2xz` — it's an untrustworthy OLS fit (noted in the
overpy spec). osm2usd does the real pyproj projection.

## Prim hierarchy (the contract the viewer depends on)

```
/World/OSM
├── Roads/{motorway,street,living,service,trail,hwother}
└── Buildings/{office,residency,parking,retail,public,farm,school,religion,ruin,bldother}
```

One merged `UsdGeom.Mesh` per class group. The bucket list is **not**
hardcoded — group by whatever `class_group` values appear in the JSON's
`class_groups` taxonomy echo; slot Roads vs Buildings by the `thing` field.

## Conventions

- **Package manager:** `uv` + venv. `usd-core` pip wheel for `pxr` (no Kit needed).
- **CLI:** `argparse` + `rich-argparse`, `rich` console summary. Short flags for
  every param (see spec CLI table). Mirror `build_usd.py`'s Console/Table style.
- **Version** in `src/osm2usd/__init__.py: __version__`; bump on every code change.
- **Tests:** offline characterization-style — a tiny committed OSM JSON + small
  synthetic DEM → assert prim hierarchy, hand-computed vertex coords, draped z.
  No network.
- **Materials:** per-class `UsdPreviewSurface` wired to `outputs:surface` (RTX
  needs a real surface shader, not bare displayColor — model on
  `build_usd.py:author_stage`, minus textures).

## Sibling repos (flat layout under `d:\senckenberg\`)

```
d:\senckenberg\
├── osm2usd\          ← you are here (generic builder)
├── messelpit\        ← Messel data pipeline; owns origin.json + dem.tif
├── messelpit_viewer\ ← Omniverse Kit viewer
└── usd_viewer\       ← generic Kit viewer (the analog for this split)
```

GitHub: [`MikeWise2718/osm2usd`](https://github.com/MikeWise2718/osm2usd).
Consumed by scene repos as an editable/path install (`uv pip install -e ../osm2usd`).

## Status

See the task tracker at the bottom of [`specs/osm2usd.md`](specs/osm2usd.md).
Tasks 1–8 done: the full `src/osm2usd/` pipeline
(`project`/`drape`/`geometry`/`group`/`materials`/`build`/`cli`) is implemented
and covered by 27 offline tests (a committed tiny synthetic DEM + tiny OSM JSON
under `tests/fixtures/`). The CLI builds a valid spec-shaped stage end to end.

Next up: **task 9** — run on the real Messel `messel_osm.json` + `dem.tif` and
eyeball in usdview (this is the first validation against the actual overpy JSON
field shapes — the parser is tolerant but unverified against real data). Then
**tasks 10–12**, the messelpit integration.

### Running the tests

```bash
uv venv && uv pip install -e . pytest
.venv/Scripts/python -m pytest -q          # 27 tests, all offline
```

Fixtures are regenerable with `python tests/fixtures/make_fixtures.py` (rewrites
`tiny_dem.tif` + `origin.json`; `tiny_osm.json` is hand-authored so its lat/lons
land at known local-meter coords).
