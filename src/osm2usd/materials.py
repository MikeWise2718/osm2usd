"""Per-class UsdPreviewSurface materials.

RTX needs a real surface shader wired to outputs:surface, not a bare
displayColor (lesson from build_usd.py + the lion enclosure). We model the
shader wiring on build_usd.py:author_stage's PBR + ConnectableAPI pattern,
minus the texture nodes -- a flat diffuse color per class group.
"""

from __future__ import annotations

from pxr import Gf, Sdf, Usd, UsdShade

# A fixed, legible, distinct palette keyed by class_group. Unknown groups get a
# neutral grey. Roads lean warm/grey, buildings lean saturated, so the two
# families read apart at a glance. (Could echo overpy `objcolor` later.)
PALETTE: dict[str, tuple[float, float, float]] = {
    # roads
    "motorway": (0.90, 0.45, 0.10),
    "street": (0.75, 0.75, 0.70),
    "living": (0.70, 0.80, 0.55),
    "service": (0.60, 0.60, 0.58),
    "trail": (0.55, 0.40, 0.25),
    "footway": (0.55, 0.40, 0.25),
    "hwother": (0.50, 0.50, 0.50),
    # buildings
    "office": (0.35, 0.45, 0.70),
    "residency": (0.80, 0.65, 0.45),
    "parking": (0.45, 0.45, 0.50),
    "retail": (0.80, 0.40, 0.45),
    "public": (0.40, 0.65, 0.70),
    "farm": (0.65, 0.55, 0.30),
    "school": (0.70, 0.60, 0.25),
    "religion": (0.55, 0.45, 0.65),
    "ruin": (0.50, 0.45, 0.40),
    "bldother": (0.60, 0.60, 0.62),
}
DEFAULT_COLOR = (0.6, 0.6, 0.6)


def color_for(class_group: str) -> tuple[float, float, float]:
    return PALETTE.get(class_group, DEFAULT_COLOR)


def define_material(
    stage: Usd.Stage, parent_path: str, class_group: str
) -> UsdShade.Material:
    """Create /World/OSM/Materials/<class_group> with a UsdPreviewSurface whose
    'surface' output is wired into the material -- the RTX-correct pattern."""
    mat_path = f"{parent_path}/{class_group}"
    material = UsdShade.Material.Define(stage, mat_path)

    pbr = UsdShade.Shader.Define(stage, f"{mat_path}/PBR")
    pbr.CreateIdAttr("UsdPreviewSurface")
    r, g, b = color_for(class_group)
    pbr.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
        Gf.Vec3f(r, g, b)
    )
    pbr.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.85)
    pbr.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)

    material.CreateSurfaceOutput().ConnectToSource(
        pbr.ConnectableAPI(), "surface"
    )
    return material


def bind(prim: Usd.Prim, material: UsdShade.Material) -> None:
    """Bind a material to a mesh prim."""
    UsdShade.MaterialBindingAPI.Apply(prim).Bind(material)
