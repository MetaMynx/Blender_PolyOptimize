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
    reduction_level: IntProperty(
        name="Vertex Reduction Level",
        description=(
            "Reduces overall detail before flattening. Each level keeps "
            "half of what the previous level kept: level 1 ≈ 50%, level 2 "
            "≈ 25%, level 3 ≈ 12.5%. 0 turns this off. (Technical: "
            "collapse decimation at ratio 0.5^level)"
        ),
        default=0,
        min=0,
        max=8,
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
    bpy.utils.register_class(PolyOptimizeSettings)
    bpy.types.Scene.poly_optimize = bpy.props.PointerProperty(
        type=PolyOptimizeSettings
    )


def unregister() -> None:
    del bpy.types.Scene.poly_optimize
    bpy.utils.unregister_class(PolyOptimizeSettings)
