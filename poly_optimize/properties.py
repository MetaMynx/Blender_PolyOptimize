"""Scene-level settings for PolyOptimize.

A single PropertyGroup is attached to the Scene so both panel locations
(Modifier Properties tab and viewport N-panel) share one set of values.

Tooltips are written in plain language first, with the standard Blender
term in parentheses, because tooltip text is fixed at registration and
cannot follow the panel's Basic/Advanced label toggle.
"""

from __future__ import annotations

import math

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
)

from . import util

UI_MODE_ITEMS = (
    ("BASIC", "Basic", "Plain-language labels — great while learning"),
    ("ADVANCED", "Advanced", "Standard Blender terminology"),
)

OUTPUT_MODE_ITEMS = (
    (
        "COPY_HIDE",
        "Copy — Hide Original",
        "Work on a duplicate and hide the original (in the viewport and "
        "in renders)",
    ),
    (
        "COPY_OFFSET",
        "Copy — Offset Beside Original",
        "Work on a duplicate placed next to the original, so you can "
        "compare them side by side",
    ),
    (
        "COPY_OVERLAP",
        "Copy — Original In Place",
        "Work on a duplicate; the original stays visible in the same spot",
    ),
    (
        "IN_PLACE",
        "In-Place — Undo to Revert",
        "Change the selected model directly (press Ctrl+Z to undo)",
    ),
)


class PolyOptimizeSettings(bpy.types.PropertyGroup):
    """User-facing options plus hidden fields storing the last run's stats."""

    ui_mode: EnumProperty(
        name="Labels",
        description="Switch between plain-language and technical labels",
        items=UI_MODE_ITEMS,
        default="BASIC",
    )
    output_mode: EnumProperty(
        name="Output",
        description="What to do with the original model",
        items=OUTPUT_MODE_ITEMS,
        default="COPY_HIDE",
    )
    offset_gap: FloatProperty(
        name="Offset Gap",
        description=(
            "Empty space between the original and the optimized copy "
            "when using the offset option — the copy is placed beside "
            "the model's width plus this gap, along the X axis"
        ),
        subtype="DISTANCE",
        default=0.5,
        min=0.0,
        soft_max=10.0,
    )
    angle_limit: FloatProperty(
        name="Planar Tolerance",
        description=(
            "How close to perfectly flat a surface must be before its faces "
            "are merged into one. Higher values flatten more aggressively "
            "but can erase gentle curves. (Technical: planar tolerance — "
            "the maximum angle between face normals for limited dissolve)"
        ),
        subtype="ANGLE",
        default=math.radians(2.0),
        min=0.0,
        soft_max=math.radians(15.0),
        max=math.radians(60.0),
    )
    detail_percent: FloatProperty(
        name="Detail Kept",
        description=(
            "How much of the model's detail to keep before flattening. "
            "100% turns reduction off; 50% keeps roughly half the "
            "triangles. In Edit Mode this applies only to the selected "
            "part of the model. (Technical: collapse-decimate ratio, "
            "expressed as a percentage)"
        ),
        subtype="PERCENTAGE",
        default=100.0,
        min=1.0,
        max=100.0,
    )
    use_weld: BoolProperty(
        name="Weld Duplicate Vertices",
        description=(
            "First fuse points that sit exactly on top of each other. "
            "Auto-generated models are often full of these, and they "
            "block faces from merging. (Technical: merge by distance / "
            "remove doubles)"
        ),
        default=True,
    )
    weld_distance: FloatProperty(
        name="Weld Distance",
        description=(
            "Points closer together than this count as overlapping and "
            "get fused"
        ),
        subtype="DISTANCE",
        default=0.0001,
        min=0.0,
        soft_max=0.01,
        precision=5,
    )
    preserve_seams: BoolProperty(
        name="Preserve UV Seams",
        description=(
            "Never merge across the cut lines where the texture wraps "
            "around the model. (Technical: delimit by UV seams)"
        ),
        default=True,
    )
    preserve_sharp: BoolProperty(
        name="Preserve Sharp Edges",
        description=(
            "Never merge across corners marked as sharp, so crisp edges "
            "stay crisp. (Technical: delimit by sharp edges)"
        ),
        default=True,
    )
    preserve_materials: BoolProperty(
        name="Preserve Material Borders",
        description=(
            "Never merge faces that have different colors/materials into "
            "one face. (Technical: delimit by material index)"
        ),
        default=True,
    )
    preserve_uv_borders: BoolProperty(
        name="Preserve UV Borders",
        description=(
            "Never merge where it would smear or stretch the texture. "
            "(Technical: delimit by UV discontinuities)"
        ),
        default=True,
    )
    simplify_boundaries: BoolProperty(
        name="Simplify Region Boundaries",
        description=(
            "Also straighten the outlines where flattened areas meet. "
            "Removes more geometry, but can open small gaps between the "
            "model's sides — leave off for watertight results. "
            "(Technical: dissolve boundaries in limited dissolve)"
        ),
        default=False,
    )
    regenerate_uvs: BoolProperty(
        name="Regenerate UVs",
        description=(
            "After optimizing, rebuild the texture layout (UV map) of "
            "the whole model — even when only a selection was optimized, "
            "so the layout stays clean. A new UV map is created if the "
            "model has none. Warning: textures painted for the old "
            "layout will no longer line up. (Technical: axis-aligned "
            "box projection with shelf-packed islands)"
        ),
        default=False,
    )
    bake_textures: BoolProperty(
        name="Bake Textures to New UVs",
        description=(
            "When rebuilding UVs on a model that already has image "
            "textures, also render ('bake') the old textures into new "
            "images that match the new layout — so the model keeps "
            "looking the same. Bakes the full PBR set as needed: colour "
            "always, plus roughness, metallic and normal/bump maps when "
            "the materials use them; plain number values are copied "
            "directly. Materials are simplified to a standard textured "
            "shader. (Technical: Cycles EMIT bakes of the base-colour, "
            "roughness and metallic channels via temporary emission "
            "routing, plus a tangent-space normal bake — from the old "
            "UV map into atlases on the rebuilt map)"
        ),
        default=True,
    )
    bake_resolution: EnumProperty(
        name="Bake Resolution",
        description="Size of the new texture image created by the bake",
        items=(
            ("512", "512 px", "Small and fast"),
            ("1024", "1024 px", "Good default"),
            ("2048", "2048 px", "High detail, slower"),
            ("4096", "4096 px", "Very high detail, slow"),
        ),
        default="1024",
    )
    triangulate: BoolProperty(
        name="Triangulate Result",
        description=(
            "Split the final flat faces into triangles — the format game "
            "engines prefer. Leave off to keep clean flat faces. "
            "(Technical: triangulate n-gons)"
        ),
        default=False,
    )

    # --- Last-run statistics (written by the operator, read by panels) ---
    has_result: BoolProperty(default=False, options={"HIDDEN"})
    last_verts_before: IntProperty(default=0, options={"HIDDEN"})
    last_verts_after: IntProperty(default=0, options={"HIDDEN"})
    last_edges_before: IntProperty(default=0, options={"HIDDEN"})
    last_edges_after: IntProperty(default=0, options={"HIDDEN"})
    last_faces_before: IntProperty(default=0, options={"HIDDEN"})
    last_faces_after: IntProperty(default=0, options={"HIDDEN"})

    def delimit_flags(self) -> frozenset[str]:
        """Translate the preserve toggles into bmesh delimit flags."""
        flags = {"NORMAL"}
        if self.preserve_seams:
            flags.add("SEAM")
        if self.preserve_sharp:
            flags.add("SHARP")
        if self.preserve_materials:
            flags.add("MATERIAL")
        if self.preserve_uv_borders:
            flags.add("UV")
        return frozenset(flags)


def register() -> None:
    util.register_class_fresh(PolyOptimizeSettings)
    bpy.types.Scene.poly_optimize = bpy.props.PointerProperty(
        type=PolyOptimizeSettings
    )


def unregister() -> None:
    if hasattr(bpy.types.Scene, "poly_optimize"):
        del bpy.types.Scene.poly_optimize
    util.unregister_class_safe(PolyOptimizeSettings)
