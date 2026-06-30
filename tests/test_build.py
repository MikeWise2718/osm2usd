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


def test_osm_root_is_xformable(osm_json, origin_json, dem_tif, tmp_path):
    """/World/OSM must be an Xform (not a Scope) so the viewer can author a
    height-exaggeration scale op on it to make the overlay track the terrain."""
    from pxr import UsdGeom
    out = tmp_path / "xform_osm.usd"
    _build(osm_json, origin_json, DemSampler.from_tif(dem_tif), out)
    stage = Usd.Stage.Open(str(out))
    osm = stage.GetPrimAtPath("/World/OSM")
    assert osm.GetTypeName() == "Xform"
    assert bool(UsdGeom.Xformable(osm))  # can carry xformOps


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


def test_points_become_named_waterhole_markers(osm_json, origin_json, dem_tif, tmp_path):
    """A borehole point yields its OWN named prim under /World/OSM/Water/waterhole/,
    draped + blue, carrying the name + base_z as customData (for tooltips +
    re-basing under exaggeration)."""
    from pxr import UsdShade

    data = load_osm_json(osm_json)
    # A point at a fixture lat/lon known to land inside the 100x100 DEM
    # (the fixture's KNOWN table maps this near local (20,60)).
    data["points"] = [
        {"name": "TestHole", "kind": "borehole",
         "lat": 49.88660374, "lng": 8.72186436},
    ]
    out = tmp_path / "pts_osm.usd"
    res = build_stage(
        data, Origin.from_json(origin_json),
        DemSampler.from_tif(dem_tif), out,
        BuildOptions(marker_radius=5.0, marker_height=8.0),
    )
    assert res.n_points == 1

    stage = Usd.Stage.Open(str(out))
    # The class group is now a Scope holding individually named markers.
    grp = stage.GetPrimAtPath("/World/OSM/Water/waterhole")
    assert grp.IsValid()
    children = grp.GetChildren()
    assert len(children) == 1
    marker = children[0]
    assert marker.GetName() == "TestHole"           # OSM name -> prim name
    # ':' nests under an 'osm2usd' sub-dict (same as osm2usd:version).
    cd = marker.GetCustomData()["osm2usd"]
    assert cd["name"] == "TestHole"
    assert abs(cd["base_z"] - 102.0) < 1e-3          # draped DEM z at x~20

    mesh = UsdGeom.Mesh(marker)
    zs = sorted({round(p[2], 2) for p in mesh.GetPointsAttr().Get()})
    assert zs[0] == 102.0 and zs[-1] == 110.0       # base + marker height 8
    binding = UsdShade.MaterialBindingAPI(marker).GetDirectBinding()
    assert binding.GetMaterial().GetPrim().IsValid()


def test_duplicate_point_names_disambiguated(osm_json, origin_json, dem_tif, tmp_path):
    """Many Kgalagadi holes share names; each must still get a unique prim."""
    data = load_osm_json(osm_json)
    data["points"] = [
        {"name": "Twin", "kind": "borehole", "lat": 49.88660374, "lng": 8.72186436},
        {"name": "Twin", "kind": "borehole", "lat": 49.88642453, "lng": 8.72214380},
    ]
    out = tmp_path / "dup_osm.usd"
    res = build_stage(
        data, Origin.from_json(origin_json),
        DemSampler.from_tif(dem_tif), out, BuildOptions(),
    )
    assert res.n_points == 2
    stage = Usd.Stage.Open(str(out))
    names = {c.GetName() for c in
             stage.GetPrimAtPath("/World/OSM/Water/waterhole").GetChildren()}
    assert len(names) == 2   # collision-disambiguated


def test_points_cropped_outside_dem(osm_json, origin_json, dem_tif, tmp_path):
    data = load_osm_json(osm_json)
    # Far outside the DEM (way north) -> cropped, not clamped.
    data["points"] = [
        {"name": "FarHole", "kind": "borehole", "lat": 50.5, "lng": 8.72186436},
    ]
    out = tmp_path / "croppts_osm.usd"
    res = build_stage(
        data, Origin.from_json(origin_json),
        DemSampler.from_tif(dem_tif), out,
        BuildOptions(crop_to_dem=True),
    )
    assert res.n_points == 0
    assert res.n_cropped >= 1
    stage = Usd.Stage.Open(str(out))
    assert not stage.GetPrimAtPath("/World/OSM/Water/waterhole").IsValid()
