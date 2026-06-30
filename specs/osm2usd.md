# osm2usd — shared OSM→USD builder + messelpit integration

> **Placement note:** This spec now lives in its real home at
> `d:\senckenberg\osm2usd\specs\osm2usd.md` (moved here when the repo was
> created — task 1). The messelpit-integration portion also gets a
> copy/pointer in **`d:\senckenberg\messelpit\specs\osm-overlay.md`** when
> tasks 10–12 land.

Spec for a **new, scene-agnostic** package that turns overpy's OSM JSON
into draped, class-grouped USD, plus the **thin per-scene glue** that
invokes it for Messel (and later Kalahari and other digital twins).

## Why a separate package (the architecture decision)

The builder is **scene-agnostic** logic: project lat/lon, extrude
footprints/ribbons, drape on *a* DEM, group by class. But `messelpit` is
**hard-wired to one scene** (its README/CLAUDE.md say so — Messel's
DGM1/DOP20 pipeline, its `origin.json`). Putting reusable logic inside a
single-scene repo would force Kalahari to either depend on a *Messel*
repo or copy-paste the builder (drift within months).

This is the **same split already validated** by
`usd_viewer` (generic) vs. `messelpit_viewer` (one scene). The OSM
builder takes the generic role from the start rather than being extracted
painfully later — justified because **Kalahari is near-term**, the exact
condition where pre-extraction beats YAGNI.

| Repo | Role | Analog |
|---|---|---|
| **`osm2usd`** (new) | Scene-agnostic builder: JSON + origin + DEM → grouped USD | `usd_viewer` |
| **`messelpit`** | Owns Messel DEM/origin; thin call into osm2usd + composition | `messelpit_viewer` |
| **`kalahari`** (future) | Owns Kalahari DEM/origin; same thin call | (future viewer) |

Layout decision: **sibling repos under `d:\senckenberg\`** (matches the
current flat layout — `messelpit`, `messelpit_viewer`, `usd_viewer`, and
now `osm2usd`). No umbrella/monorepo reorg now; revisit at 3+ twins.

## The contract osm2usd sits between

```
overpy  ──<area>_osm.json──>  osm2usd  ──<scene>_osm.usd──>  messelpit composition
(raw lat/lon, class,          (project + extrude +          (reference under
 type, height, levels)         drape + group)                /World/OSM of base)
                                   ▲
                          origin.json + dem.tif
                          (supplied by the scene repo)
```

osm2usd is **pure**: every scene-specific input is a parameter. It never
imports messelpit, never hardcodes an origin, never assumes Messel.

## Inputs (all parameters — nothing scene-specific baked in)

1. **OSM JSON** — overpy's `<area>_osm.json` (schema in
   `overpy/specs/osm-json-export-for-usd.md`): `ways[]` with `[lat,lng]`
   nodes, `thing`, `type`, `class_group`, `height_m`, `levels`, `closed`,
   plus a `class_groups` taxonomy echo.
2. **Projection origin** — EPSG code + SW-corner easting/northing. For
   Messel this comes straight from `messelpit/data/prep/origin.json`
   (`epsg: 25832`, `utm_sw_easting: 480000`, `utm_sw_northing: 5526000`).
   osm2usd reads those fields; it does not know they're "Messel".
3. **DEM (optional but required for good results)** — a local-frame
   `dem.tif`. **Key alignment fact:** messelpit's `prep_rasters.py` writes
   the DEM with `from_origin(0.0, height_m, 1.0, 1.0)`, `crs=None` — i.e.
   **already in local meters, SW corner at (0,0), +Y north**, the *same*
   frame `build_usd.py:build_grid_mesh` uses
   (`x=col·res`, `y=(H-1-row)·res`, `z=dem[row,col]`). So after projecting
   a node to local meters, draping is a **direct array lookup**, no CRS
   round-trip at sample time.

## Core pipeline (osm2usd internals)

### 1. Project lat/lon → scene-local meters

Per node: `lat/lon (EPSG:4326) → EPSG:<origin.epsg> (pyproj) → subtract
(easting0, northing0) → (x_local, y_local)`. This yields the same
local-meters, +Y-north, Z-up frame the terrain uses. **Reuse the exact
conversion already validated** in the README coordinate tables (pyproj /
rasterio.warp). pyproj is the clean dependency; the messelpit repo proved
rasterio.warp also works if pyproj is unavailable.

> Do **not** use overpy's `ll2xz` — it's an untrustworthy OLS fit (noted
> in the overpy spec). osm2usd does the real projection.

#### Two scene frames (`origin.mode`, auto-detected)

`Origin.from_json` detects the frame from the origin.json keys, and
`make_projector(origin)` returns the matching projector:

- **`utm`** (Messel) — `epsg` + `utm_sw_easting` + `utm_sw_northing`. The
  pyproj path above (`UtmProjector`). Unchanged.
- **`degree_grid`** (Kalahari) — `extent_deg` + `width_m` + `height_m`
  (+ optional `sw_lon`/`sw_lat`). The terrain is a *geographic* raster, not
  a metric projection, so local meters are a **linear deg→m remap** of the
  extent box (`DegreeGridProjector`, no pyproj):

  ```
  x = (lon - sw_lon) / extent_w_deg * width_m
  y = (lat - sw_lat) / extent_h_deg * height_m     # +Y north (lat up)
  ```

  This is the exact inverse of `build_grid_mesh`'s `px = col*res_x`,
  `py = (H-1-row)*res_y`. Nodes west/south of the SW corner get negative
  local coords (no clamp at projection time — only the DEM sampler clamps).

### 2. Drape onto the DEM

For each projected vertex `(x, y)`:
- Map to DEM pixel: `col = round(x / res_x)`, `row = (H-1) - round(y / res_y)`
  (inverse of `build_grid_mesh`'s mapping). **`res_x` and `res_y` are
  per-axis** (read from the GeoTIFF transform's `a`/`e`): square for Messel
  (1 m), anisotropic for Kalahari's degree grid (~5.04 m X / ~5.54 m Y).
- `z = dem[row, col]` (clamp to bounds; nodes outside the DEM bbox →
  clamp to edge or drop, log either way — see open questions).
- **Roads:** sample z **per vertex** so ribbons follow undulation.
- **Buildings:** sample z at the footprint and **use one base z** (e.g.
  min or mean of footprint samples) so the building sits flat, not
  tilted. Extrude up from that base.
- If no DEM supplied: z=0 everywhere (flat) — only useful for debugging;
  warn loudly. Messel's 60 m relief makes flat placement obviously wrong.

### 3. Extrude geometry

- **Buildings** (`closed:true`): triangulate the footprint polygon
  (ear-clipping; handle the umlaut/relation cases overpy already
  resolved). Height priority: `height_m` → `levels × LEVEL_HEIGHT` (~3 m)
  → `DEFAULT_BLD_HEIGHT` (~6 m). Build walls (extrude edges) + a cap.
- **Roads** (`closed:false`): extrude the polyline to a flat ribbon of a
  per-class width (a small `CLASS_WIDTH` table keyed by class_group:
  motorway wide, footway narrow). Lay the ribbon on the draped z.
  (`BasisCurves` is an alternative but meshes are safer for RTX + VR —
  start with ribbons.)

### 4. Group by class into the toggleable hierarchy

One `UsdGeom.Mesh` **per class group**, merged for draw-call sanity
(Messel could be thousands of ways), parented exactly as the viewer's
visibility tab expects:

```
/World/OSM
├── Roads/{motorway,street,living,service,trail,hwother}
└── Buildings/{office,residency,parking,retail,public,farm,school,religion,ruin,bldother}
```

(14 class groups + 2 catch-alls — the taxonomy travels in the JSON's
`class_groups`, so osm2usd doesn't hardcode the bucket list; it groups by
whatever `class_group` values appear, slotting Roads vs Buildings by the
`thing` field.)

### 5. Materials

A `UsdPreviewSurface` per class group, distinct legible color per bucket.
**Lesson from build_usd.py + the lion enclosure:** RTX needs a real
surface shader wired to `outputs:surface` (not bare displayColor). Model
the shader-wiring on `build_usd.py:author_stage` (PBR + the
ConnectableAPI pattern), minus the texture nodes.

### 6. Stage conventions (match the terrain exactly)

`metersPerUnit = 1.0`, `UpAxis = Z`, default prim `/World`. Stamp
`customData["osm2usd:version"]` and the source area/origin on `/World/OSM`
so the stage is self-describing. (Optionally stamp an
`overlay_groups` customData listing the subtree paths so the viewer can
auto-discover them — see usd_viewer spec's v2 note.)

## CLI (global Python conventions: uv, rich, rich-argparse, short flags)

```
osm2usd build -j messel_osm.json -o messel_osm.usd \
    --origin messelpit/data/prep/origin.json \
    [-dem messelpit/data/prep/dem.tif] \
    [--default-height 6.0] [--level-height 3.0] [--road-width-scale 1.0]
```

| Flag | Short | Meaning |
|---|---|---|
| `--json` | `-j` | overpy OSM JSON (required) |
| `--out` | `-o` | output `*_osm.usd` (required) |
| `--origin` | `-or` | origin.json (epsg + sw easting/northing) (required) |
| `--dem` | `-dm` | local-frame dem.tif for draping (optional, strongly recommended) |
| `--default-height` | `-dh` | fallback building height m (default 6) |
| `--level-height` | `-lh` | meters per building level (default 3) |
| `--road-width-scale` | `-rw` | global multiplier on per-class ribbon widths |

rich console summary (N buildings, N roads, per-class counts, z range,
% nodes outside DEM, output path/size). Mirror `build_usd.py`'s
Console/Table style.

Origin may alternatively be passed as explicit `--epsg/--easting/--northing`
for scenes without an origin.json, but origin.json is the Messel path.

## Package skeleton (osm2usd repo)

```
osm2usd/
├── README.md                     purpose, the JSON↔origin↔DEM contract, uv setup
├── CLAUDE.md                     scene-agnostic invariant; "never import a scene repo"
├── pyproject.toml                uv; deps: pxr (usd-core), pyproj, rasterio, numpy, rich, rich-argparse
├── specs/osm2usd.md              this spec (moved here)
├── src/osm2usd/
│   ├── __init__.py               __version__
│   ├── project.py                lat/lon → local meters (origin-relative)
│   ├── drape.py                  DEM sampler (local-frame array lookup)
│   ├── geometry.py               footprint triangulation + road ribbons
│   ├── group.py                  class→/World/OSM/{Roads,Buildings}/{group} authoring
│   ├── materials.py              per-class UsdPreviewSurface
│   ├── build.py                  orchestration: JSON → stage
│   └── cli.py                    argparse + rich-argparse entry
└── tests/
    ├── fixtures/                 tiny OSM JSON + tiny synthetic DEM
    └── test_*.py                 projection round-trip, drape lookup, grouping, no-network
```

Characterization-style tests (echoing the overpy approach): a tiny
committed OSM JSON + a small synthetic DEM → assert prim hierarchy,
vertex placement against hand-computed expected coords, draped z matches
the synthetic DEM. All offline.

## messelpit integration (the thin per-scene glue)

messelpit stays the Messel owner. It gains:

1. **A copy of `messel_osm.json`** in `data/` (produced by overpy; commit
   if license-clear — OSM is ODbL, redistributable with attribution).
2. **A thin build step** — either:
   - a `tools/build_osm_overlay.ps1` (matching the existing
     `build_variants.ps1` style) that calls
     `osm2usd build -j data/messel_osm.json -o out/messel_osm.usd
     --origin data/prep/origin.json --dem data/prep/dem.tif`, **or**
   - a tiny `src/messelpit/build_osm.py` wrapper if a Python entry is
     preferred. Lean to the PS1 to match existing tooling.
3. **Composition onto the base** — reference `messel_osm.usd` under
   `/World/OSM` of `messel_lo.usd` (and/or `messel_med.usd`). A small
   authoring step (Sdf reference, or a `messel_lo_with_osm.usd` wrapper
   stage that sublayers both). **Recommended: reference**, so the base
   stays pristine and the overlay toggles wholesale. Confirm at impl.
4. **README/CLAUDE.md updates** documenting the overlay step and the new
   `osm2usd` dependency.

osm2usd is consumed as: a sibling-repo editable install
(`uv pip install -e ../osm2usd`) or a path dependency. Keep it loose; both
repos are local.

## Sequencing

1. **osm2usd first**, tested against a **synthetic DEM + tiny JSON** — no
   Messel data needed to validate projection/drape/grouping.
2. Then point it at the **real Messel** `messel_osm.json` + `dem.tif`.
3. Then the **composition** onto `messel_lo.usd`.
4. The **viewer visibility tab** (usd_viewer tasks 12–17) can proceed in
   parallel against a hand-authored stub — it only needs the prim-path
   hierarchy, which this spec fixes.

## Task tracker

| # | Task | Repo | Status |
|---|------|------|--------|
| 1 | Create `osm2usd` repo (pyproject/uv, README, CLAUDE.md, git+remote) | osm2usd | ☑ |
| 2 | `project.py` — lat/lon → local meters (pyproj), origin-relative | osm2usd | ☑ |
| 3 | `drape.py` — DEM sampler matching prep_rasters local frame | osm2usd | ☑ |
| 4 | `geometry.py` — footprint ear-clip + road ribbon extrude | osm2usd | ☑ |
| 5 | `group.py` — /World/OSM/{Roads,Buildings}/{class} authoring | osm2usd | ☑ |
| 6 | `materials.py` — per-class UsdPreviewSurface (RTX-correct wiring) | osm2usd | ☑ |
| 7 | `build.py` + `cli.py` — orchestration + argparse/rich entry | osm2usd | ☑ |
| 8 | Tests: synthetic DEM + tiny JSON, offline; projection/drape/grouping | osm2usd | ☑ |
| 9 | Run on real Messel JSON + dem.tif; eyeball in usdview | osm2usd→messelpit | ☑ ran; 2104 blds + 1206 roads, draped Z 104–233 m, 3.4% off-DEM clamped |
| 10 | messelpit: commit messel_osm.json + build_osm_overlay step | messelpit | ☑ data/messel_osm.json + tools/build_osm_overlay.ps1 + src/messelpit/build_overlay_stage.py |
| 11 | messelpit: reference messel_osm.usd under /World/OSM of messel_lo (wrapper) | messelpit | ☑ out/messel_lo_with_osm.usd; verified (17 meshes, /World/{Terrain,OSM}) |
| 12 | messelpit + osm2usd README/CLAUDE updates | both | ☐ |
| 13 | (later) Kalahari: its origin.json + DEM + same osm2usd call | kalahari | ☐ |
| 14 | Eyeball in usdview / the USD viewer (visual QA — not yet done headless-only) | — | ☐ |
| 15 | usd_viewer: build the visibility tab + Messel sidecar (separate spec) | usd_viewer | ☐ |

### Notes from the first real run (2026-06-13)

- **overpy 406 fix (in overpy repo):** `overpass-api.de` now rejects
  requests with no User-Agent (HTTP 406); overpy 0.7 sends none. Fixed by
  installing a global urllib opener with a UA in
  `overpy/src/overpy_processor/core/osm_query.py`. Added a `messel`
  scenario to `overpy/osmscenarios/scenarios.json` (bbox = origin.json
  WGS84 corners, `subgroup: "all"`). overpy then exported 3310 ways
  (2104 buildings + 1206 roads, 14763 nodes, 2.1 MB).
- **No `height` tags on Messel buildings** — all use `building:levels` or
  the default; the `levels × level_height` fallback carries placement.
- **Composition = wrapper stage (DECIDED).** `messel_lo_with_osm.usd`
  references the untouched `messel_lo.usd` + `messel_osm.usd` onto one
  `/World`. Base file never edited.

## Open questions (resolve at implementation)

- **Off-DEM spill:** OSM bbox X/Y runs −1750..7778 × −849..9676 vs. the
  terrain's 0..6000 × 0..9000 — the 3.4% off-DEM ways sit at/beyond the
  bbox edge (clamped in Z, real X/Y kept), so some overlay hangs past the
  terrain. Options: a `--crop-to-dem` flag in osm2usd, tighten the overpy
  query bbox, or accept the spill. (Was "clamp to edge vs drop vs flat-z";
  clamp is what shipped, but the X/Y spill is the visible side effect.)
- **Building base z:** min vs. mean of footprint samples (leaning min, so
  no corner floats above ground; revisit on sloped sites).
- **Road geometry:** flat ribbons vs. BasisCurves (leaning ribbons).
- **osm2usd ↔ usd-core:** confirm pxr availability outside Kit (usd-core
  pip wheel — the usd_viewer repo already used a usd-core venv per
  gitignore notes; same approach here).
- **Color palette:** fixed per-class palette in materials.py (legible,
  distinct); could echo overpy `objcolor` later.

## References

- Downstream viewer overlay/visibility tab + prim-path contract:
  `usd_viewer/specs/osm-overlay-and-visibility-tab.md`
- Upstream JSON producer + schema:
  `overpy/specs/osm-json-export-for-usd.md`
- Messel DEM/origin frame to match: `messelpit/tools/prep_rasters.py`
  (`from_origin(0, height_m, 1, 1)`, `crs=None`) and
  `messelpit/src/messelpit/build_usd.py` (`build_grid_mesh` coord mapping)
- Origin truth: `messelpit/data/prep/origin.json`
- Shader-wiring reference (RTX-correct): `build_usd.py:author_stage`
```
