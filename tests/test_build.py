"""End-to-end build: JSON + origin + DEM -> stage; assert hierarchy + draping."""

from __future__ import annotations

from pxr import Usd, UsdGeom

from osm2usd.build import (
    BuildOptions,
    building_height,
    build_stage,
    load_osm_json,
)
from osm2usd.drape import DemSampler, FlatSampler
from osm2usd.project import Origin


def _build(osm_json, origin_json, sampler, out):
    return build_stage(
        load_osm_json(osm_json),
        Origin.from_json(origin_json),
        sampler,
        out,
        BuildOptions(),
    )


def test_hierarchy_and_counts(osm_json, origin_json, dem_tif, tmp_path):
    out = tmp_path / "fixture_osm.usd"
    res = _build(osm_json, origin_json, DemSampler.from_tif(dem_tif), out)

    assert res.n_buildings == 2
    assert res.n_roads == 1
    assert out.exists()

    stage = Usd.Stage.Open(str(out))
    assert stage.GetPrimAtPath("/World/OSM").IsValid()
    # office + bldother buildings, street road -> three class-group meshes.
    assert stage.GetPrimAtPath("/World/OSM/Buildings/office").IsValid()
    assert stage.GetPrimAtPath("/World/OSM/Buildings/bldother").IsValid()
    assert stage.GetPrimAtPath("/World/OSM/Roads/street").IsValid()

    office = UsdGeom.Mesh(stage.GetPrimAtPath("/World/OSM/Buildings/office"))
    assert office.GetPrim().IsValid()
    assert len(office.GetFaceVertexCountsAttr().Get()) == 10  # cap+walls


def test_stage_conventions(osm_json, origin_json, dem_tif, tmp_path):
    out = tmp_path / "conv_osm.usd"
    _build(osm_json, origin_json, DemSampler.from_tif(dem_tif), out)
    stage = Usd.Stage.Open(str(out))
    assert stage.GetMetadata("metersPerUnit") == 1.0
    assert UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z
    assert stage.GetDefaultPrim().GetPath() == "/World"


def test_customdata_self_describing(osm_json, origin_json, dem_tif, tmp_path):
    out = tmp_path / "meta_osm.usd"
    _build(osm_json, origin_json, DemSampler.from_tif(dem_tif), out)
    stage = Usd.Stage.Open(str(out))
    osm = stage.GetPrimAtPath("/World/OSM")
    # SetCustomDataByKey treats ':' as a namespace separator, so these nest
    # under an 'osm2usd' sub-dict.
    cd = osm.GetCustomData()["osm2usd"]
    assert "version" in cd
    assert cd["origin"]["epsg"] == 25832
    assert len(cd["overlay_groups"]) == 3


def test_materials_bound(osm_json, origin_json, dem_tif, tmp_path):
    from pxr import UsdShade
    out = tmp_path / "mat_osm.usd"
    _build(osm_json, origin_json, DemSampler.from_tif(dem_tif), out)
    stage = Usd.Stage.Open(str(out))
    office = stage.GetPrimAtPath("/World/OSM/Buildings/office")
    binding = UsdShade.MaterialBindingAPI(office).GetDirectBinding()
    mat = binding.GetMaterial()
    assert mat.GetPrim().IsValid()
    # The surface output must be connected (RTX needs a real surface shader).
    assert mat.GetSurfaceOutput().HasConnectedSource()


def test_draped_z_matches_dem(osm_json, origin_json, dem_tif, tmp_path):
    """Office footprint spans x in [10,40]; base z = min = 100 + 10*0.1 = 101.
    The wall bottom verts must sit at that draped base."""
    out = tmp_path / "z_osm.usd"
    _build(osm_json, origin_json, DemSampler.from_tif(dem_tif), out)
    stage = Usd.Stage.Open(str(out))
    office = UsdGeom.Mesh(stage.GetPrimAtPath("/World/OSM/Buildings/office"))
    zs = sorted({round(p[2], 2) for p in office.GetPointsAttr().Get()})
    assert zs[0] == 101.0          # draped base
    assert zs[-1] == 101.0 + 12.0  # base + height_m


def test_building_height_priority():
    opts = BuildOptions(default_height=6.0, level_height=3.0)
    assert building_height({"height_m": 9.0, "levels": 2}, opts) == 9.0
    assert building_height({"height_m": None, "levels": 4}, opts) == 12.0
    assert building_height({"height_m": None, "levels": None}, opts) == 6.0


def test_flat_sampler_path(osm_json, origin_json, tmp_path):
    out = tmp_path / "flat_osm.usd"
    res = _build(osm_json, origin_json, FlatSampler(), out)
    assert not res.used_dem
    stage = Usd.Stage.Open(str(out))
    office = UsdGeom.Mesh(stage.GetPrimAtPath("/World/OSM/Buildings/office"))
    zs = sorted({round(p[2], 2) for p in office.GetPointsAttr().Get()})
    assert zs == [0.0, 12.0]  # flat base, extruded by height_m
