"""osm2usd command-line entry: argparse + rich-argparse, rich summary."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich_argparse import RichHelpFormatter

from . import __version__
from .build import BuildOptions, build_stage, load_osm_json
from .drape import DemSampler, FlatSampler
from .project import Origin


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="osm2usd",
        description="Turn overpy OSM JSON into draped, class-grouped USD.",
        formatter_class=RichHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"osm2usd {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser(
        "build", help="Build a *_osm.usd from OSM JSON.",
        formatter_class=RichHelpFormatter,
    )
    b.add_argument("-j", "--json", required=True, metavar="PATH",
                   help="overpy OSM JSON (required)")
    b.add_argument("-o", "--out", required=True, metavar="PATH",
                   help="output *_osm.usd (required)")
    b.add_argument("-or", "--origin", metavar="PATH",
                   help="origin.json (UTM: epsg + utm_sw_easting/northing; "
                        "or degree grid: extent_deg + width_m + height_m)")
    b.add_argument("--epsg", type=int,
                   help="EPSG code (instead of --origin)")
    b.add_argument("--easting", type=float,
                   help="SW-corner easting in meters (instead of --origin)")
    b.add_argument("--northing", type=float,
                   help="SW-corner northing in meters (instead of --origin)")
    b.add_argument("-dm", "--dem", metavar="PATH",
                   help="local-frame dem.tif for draping (recommended)")
    b.add_argument("-dh", "--default-height", type=float, default=6.0,
                   help="fallback building height m (default 6)")
    b.add_argument("-lh", "--level-height", type=float, default=3.0,
                   help="meters per building level (default 3)")
    b.add_argument("-rw", "--road-width-scale", type=float, default=1.0,
                   help="global multiplier on per-class ribbon widths")
    return p


def _resolve_origin(args, console: Console) -> Origin:
    if args.origin:
        return Origin.from_json(args.origin)
    if args.epsg is not None and args.easting is not None and args.northing is not None:
        return Origin(epsg=args.epsg, easting=args.easting, northing=args.northing)
    console.print(
        "[red]error:[/red] supply --origin OR all of --epsg/--easting/--northing"
    )
    sys.exit(2)


def _summary(console: Console, result, out_path: Path) -> None:
    table = Table(title="osm2usd build", show_header=False)
    table.add_column(style="cyan")
    table.add_column()
    table.add_row("Buildings", f"{result.n_buildings:,}")
    table.add_row("Roads", f"{result.n_roads:,}")
    if result.n_skipped:
        table.add_row("Skipped ways", f"{result.n_skipped:,}")
    table.add_row("Z range", f"{result.z_min:.1f} .. {result.z_max:.1f} m")
    table.add_row("Off-DEM (clamped)", f"{result.pct_off_dem:.1f}%")
    table.add_row("DEM draping", "yes" if result.used_dem else "NO (flat z=0)")
    table.add_row("Up axis / units", "Z / meters")
    size = out_path.stat().st_size if out_path.exists() else 0
    table.add_row("Output", f"{out_path}  ({size / 1024:.0f} KiB)")
    console.print(table)

    if result.per_class:
        pc = Table(title="Per-class (triangles)", show_header=True)
        pc.add_column("class group", style="cyan")
        pc.add_column("tris", justify="right")
        for name, n in sorted(result.per_class.items()):
            pc.add_row(name, f"{n:,}")
        console.print(pc)


def main(argv: list[str] | None = None) -> int:
    console = Console()
    args = _build_parser().parse_args(argv)

    if args.command == "build":
        origin = _resolve_origin(args, console)
        osm_json = load_osm_json(args.json)

        if args.dem:
            sampler = DemSampler.from_tif(args.dem)
        else:
            console.print(
                "[yellow]warning:[/yellow] no --dem supplied; placing all "
                "geometry flat at z=0 (debugging only)."
            )
            sampler = FlatSampler()

        opts = BuildOptions(
            default_height=args.default_height,
            level_height=args.level_height,
            road_width_scale=args.road_width_scale,
        )
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        result = build_stage(osm_json, origin, sampler, out_path, opts)
        _summary(console, result, out_path)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
