# osm2usd

Turn OpenStreetMap data into draped, class-grouped USD for digital-twin scenes.

`osm2usd` is a **scene-agnostic** builder: give it overpy's OSM JSON, a
projection origin, and a local-frame DEM, and it produces a `*_osm.usd` with
buildings and roads projected, extruded, draped onto the terrain, and grouped
under a toggleable `/World/OSM` hierarchy — ready to reference into a base
terrain stage.

It is the generic half of a deliberate split. Scene repos (`messelpit`, and
later `kalahari`) own their DEM and origin and make a thin call into this
package, exactly mirroring `usd_viewer` (generic) vs `messelpit_viewer` (one
scene).

```
overpy ──<area>_osm.json──> osm2usd ──<scene>_osm.usd──> scene composition
                               ▲
                       origin.json + dem.tif   (supplied by the scene repo)
```

## What it does

1. **Project** each OSM node `lat/lon (EPSG:4326)` → the scene's UTM EPSG via
   `pyproj`, then subtract the SW-corner easting/northing → scene-local meters
   (+Y north, Z up).
2. **Drape** every vertex onto the DEM by direct array lookup (the DEM is
   already in the same local frame — no CRS round-trip at sample time). Roads
   sample z per vertex to follow undulation; buildings use one base z so they
   sit flat.
3. **Extrude** — buildings (ear-clipped footprint + walls + cap, height from
   `height_m` → `levels × level-height` → default) and roads (per-class-width
   flat ribbons).
4. **Group** into one merged mesh per class group under
   `/World/OSM/{Roads,Buildings}/{class}`.
5. **Materials** — a per-class `UsdPreviewSurface` (RTX-correct shader wiring).

## Install

Requires Python ≥ 3.11. Uses [`uv`](https://docs.astral.sh/uv/).

```bash
uv venv
uv pip install -e .
```

`pxr` comes from the `usd-core` pip wheel — no Omniverse Kit install needed.

## Usage

```bash
osm2usd build -j messel_osm.json -o messel_osm.usd \
    --origin ../messelpit/data/prep/origin.json \
    -dm ../messelpit/data/prep/dem.tif \
    [--default-height 6.0] [--level-height 3.0] [--road-width-scale 1.0]
```

| Flag | Short | Meaning |
|---|---|---|
| `--json` | `-j` | overpy OSM JSON (required) |
| `--out` | `-o` | output `*_osm.usd` (required) |
| `--origin` | `-or` | `origin.json` — EPSG + SW easting/northing (required) |
| `--dem` | `-dm` | local-frame `dem.tif` for draping (optional, strongly recommended) |
| `--default-height` | `-dh` | fallback building height in m (default 6) |
| `--level-height` | `-lh` | meters per building level (default 3) |
| `--road-width-scale` | `-rw` | global multiplier on per-class ribbon widths |

The origin can alternatively be passed as explicit `--epsg/--easting/--northing`
for scenes without an `origin.json`. Without a DEM, geometry is placed flat
(z=0) — debugging only; the tool warns loudly.

A `rich` summary reports building/road counts, per-class counts, z range,
% of nodes outside the DEM, and the output path/size.

## The `/World/OSM` hierarchy

```
/World/OSM
├── Roads/{motorway,street,living,service,trail,hwother}
└── Buildings/{office,residency,parking,retail,public,farm,school,religion,ruin,bldother}
```

The bucket list isn't hardcoded — osm2usd groups by whatever `class_group`
values appear in the JSON's `class_groups` taxonomy echo, slotting Roads vs
Buildings by the `thing` field. This is the prim-path contract the viewer's
visibility tab depends on.

## Inputs

- **OSM JSON** — overpy's `<area>_osm.json` (`ways[]` with `[lat,lng]` nodes,
  `thing`, `type`, `class_group`, `height_m`, `levels`, `closed`, plus a
  `class_groups` taxonomy echo).
- **Origin** — EPSG code + SW-corner easting/northing (e.g. Messel: EPSG 25832,
  easting 480000, northing 5526000).
- **DEM** (optional, recommended) — a local-frame `dem.tif`: meters, SW corner
  at (0,0), +Y north — the same frame the terrain mesh uses.

## Data & licensing

OpenStreetMap data is © OpenStreetMap contributors, licensed
[ODbL](https://www.openstreetmap.org/copyright). Outputs derived from OSM carry
the same attribution requirement.

## Project layout

```
osm2usd/
├── README.md            ← this file
├── CLAUDE.md            ← scene-agnostic invariant + conventions
├── pyproject.toml       ← uv; deps: usd-core, pyproj, rasterio, numpy, rich, rich-argparse
├── specs/osm2usd.md     ← full spec + task tracker
├── src/osm2usd/
│   ├── project.py       ← lat/lon → local meters
│   ├── drape.py         ← DEM sampler (local-frame array lookup)
│   ├── geometry.py      ← footprint triangulation + road ribbons
│   ├── group.py         ← /World/OSM/{Roads,Buildings}/{group} authoring
│   ├── materials.py     ← per-class UsdPreviewSurface
│   ├── build.py         ← orchestration: JSON → stage
│   └── cli.py           ← argparse + rich-argparse entry
└── tests/               ← offline: tiny OSM JSON + synthetic DEM
```

The full `src/osm2usd/` pipeline is implemented and covered by an offline test
suite (`tests/` — 27 tests against a committed tiny synthetic DEM + tiny OSM
JSON). See [`specs/osm2usd.md`](specs/osm2usd.md) for the task tracker; the next
step is running it on real Messel data and wiring the messelpit overlay.

```bash
uv venv && uv pip install -e . pytest
.venv/Scripts/python -m pytest -q
```

## Related repos

- [`messelpit`](https://github.com/MikeWise2718/messelpit) — Messel data
  pipeline; produces the base terrain USD and supplies `origin.json` + `dem.tif`.
- [`messelpit_viewer`](https://github.com/MikeWise2718/messelpit_viewer) —
  Omniverse Kit viewer for the Messel scene.
