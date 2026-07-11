"""UI panels for PolyOptimize.

The same draw logic is registered in two places: the Modifier Properties
tab (wrench icon, alongside the modifier stack) and the 3D viewport
N-panel. Both read the shared Scene-level settings.

A Basic/Advanced toggle at the top of the panel switches label text
between plain language and standard Blender terminology. Tooltips are
fixed at registration, so they carry both (see properties.py).
"""

from __future__ import annotations

import bpy

from . import util
from .operators import OBJECT_OT_poly_optimize, SUPPORTED_MODES

# Property/heading/button labels as (basic, advanced) pairs. Only the
# text shown in the panel changes; property names and tooltips do not.
_LABELS: dict[str, tuple[str, str]] = {
    "output_mode": ("Result", "Output"),
    "angle_limit": ("Flatness Sensitivity", "Planar Tolerance"),
    "simplify_boundaries": (
        "Straighten Outlines", "Simplify Region Boundaries"
    ),
    "triangulate": ("Convert to Triangles", "Triangulate Result"),
    "detail_percent": ("Detail Kept", "Decimate Ratio"),
    "use_weld": ("Merge Overlapping Points", "Weld Duplicate Vertices"),
    "weld_distance": ("Merge Distance", "Weld Distance"),
    "preserve_seams": ("Protect Texture Seams", "Preserve UV Seams"),
    "preserve_sharp": ("Protect Sharp Corners", "Preserve Sharp Edges"),
    "preserve_materials": (
        "Protect Color Boundaries", "Preserve Material Borders"
    ),
    "preserve_uv_borders": (
        "Protect Texture Layout", "Preserve UV Borders"
    ),
    "heading_planar": ("Flatten Surfaces", "Planar Merge"),
    "heading_reduce": ("Detail", "Vertex Reduction"),
    "heading_cleanup": ("Tidy Up", "Cleanup"),
    "heading_preserve": ("Protect", "Preserve"),
    "button": ("Simplify Model", "Optimize Polygons"),
}


def _draw(layout: bpy.types.UILayout, context: bpy.types.Context) -> None:
    settings = context.scene.poly_optimize
    basic = settings.ui_mode == "BASIC"

    def label(key: str) -> str:
        pair = _LABELS[key]
        return pair[0] if basic else pair[1]

    row = layout.row()
    row.prop(settings, "ui_mode", expand=True)

    layout.use_property_split = True
    layout.use_property_decorate = False

    layout.prop(settings, "output_mode", text=label("output_mode"))

    col = layout.column(heading=label("heading_planar"))
    col.prop(settings, "angle_limit", text=label("angle_limit"))
    col.prop(
        settings, "simplify_boundaries", text=label("simplify_boundaries")
    )
    col.prop(settings, "triangulate", text=label("triangulate"))

    col = layout.column(heading=label("heading_reduce"))
    col.prop(
        settings, "detail_percent", text=label("detail_percent"),
        slider=True,
    )
    if settings.detail_percent >= 99.999:
        text = (
            "100% = no reduction"
            if basic
            else "Ratio 1.0 = decimation disabled"
        )
        col.label(text=text, icon="INFO")

    col = layout.column(heading=label("heading_cleanup"))
    col.prop(settings, "use_weld", text=label("use_weld"))
    sub = col.column()
    sub.active = settings.use_weld
    sub.prop(settings, "weld_distance", text=label("weld_distance"))

    col = layout.column(heading=label("heading_preserve"))
    col.prop(settings, "preserve_seams", text=label("preserve_seams"))
    col.prop(settings, "preserve_sharp", text=label("preserve_sharp"))
    col.prop(
        settings, "preserve_materials", text=label("preserve_materials")
    )
    col.prop(
        settings, "preserve_uv_borders", text=label("preserve_uv_borders")
    )

    layout.separator()
    if context.mode == "EDIT_MESH":
        layout.label(
            text="Applies to the selection, directly on the model",
            icon="INFO",
        )
    elif context.mode != "OBJECT":
        layout.label(text="Switch to Object Mode to run", icon="ERROR")
    else:
        layout.label(
            text="Settings apply when you click the button", icon="INFO"
        )
    layout.operator(
        OBJECT_OT_poly_optimize.bl_idname,
        text=label("button"),
        icon="MOD_DECIM",
    )

    obj = context.active_object
    if obj and obj.type == "MESH":
        mesh = obj.data
        box = layout.box()
        box.label(text=f"Active: {obj.name}", icon="MESH_DATA")
        box.label(
            text=(
                f"Verts {len(mesh.vertices):,} | "
                f"Edges {len(mesh.edges):,} | "
                f"Faces {len(mesh.polygons):,}"
            )
        )

    if settings.has_result:
        box = layout.box()
        box.label(text="Last Result", icon="CHECKMARK")
        _stat_row(box, "Verts", settings.last_verts_before,
                  settings.last_verts_after)
        _stat_row(box, "Edges", settings.last_edges_before,
                  settings.last_edges_after)
        _stat_row(box, "Faces", settings.last_faces_before,
                  settings.last_faces_after)


def _stat_row(
    layout: bpy.types.UILayout, label: str, before: int, after: int
) -> None:
    percent = ((before - after) / before * 100.0) if before else 0.0
    layout.label(
        text=f"{label}: {before:,} -> {after:,}  (-{percent:.1f}%)"
    )


class VIEW3D_PT_poly_optimize(bpy.types.Panel):
    """PolyOptimize panel in the 3D viewport sidebar (N-panel)."""

    bl_label = "PolyOptimize"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "PolyOptimize"

    def draw(self, context: bpy.types.Context) -> None:
        _draw(self.layout, context)


class PROPERTIES_PT_poly_optimize(bpy.types.Panel):
    """PolyOptimize panel in the Modifier Properties tab."""

    bl_label = "PolyOptimize"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "modifier"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        obj = context.active_object
        return obj is not None and obj.type == "MESH"

    def draw(self, context: bpy.types.Context) -> None:
        _draw(self.layout, context)


def _add_modifier_menu(
    self: bpy.types.Menu, context: bpy.types.Context
) -> None:
    """Entry appended to the Add Modifier dropdown.

    A Python add-on cannot register a native modifier type, so this entry
    runs the PolyOptimize operator directly (with the current panel
    settings) when picked.
    """
    obj = context.object
    if obj is None or obj.type != "MESH":
        return
    layout = self.layout
    layout.separator()
    layout.operator(
        OBJECT_OT_poly_optimize.bl_idname,
        text="PolyOptimize",
        icon="MOD_DECIM",
    )


# The Add Modifier dropdown is a nested menu since Blender 4.0; prefer the
# Edit column, fall back to the root menu if a future version renames it.
_MENU_CANDIDATES = ("OBJECT_MT_modifier_add_edit", "OBJECT_MT_modifier_add")
_CLASSES = (VIEW3D_PT_poly_optimize, PROPERTIES_PT_poly_optimize)
_appended_menu: type | None = None


def register() -> None:
    global _appended_menu
    for cls in _CLASSES:
        util.register_class_fresh(cls)
    for name in _MENU_CANDIDATES:
        menu = getattr(bpy.types, name, None)
        if menu is not None:
            menu.append(_add_modifier_menu)
            _appended_menu = menu
            break


def unregister() -> None:
    global _appended_menu
    if _appended_menu is not None:
        _appended_menu.remove(_add_modifier_menu)
        _appended_menu = None
    for cls in reversed(_CLASSES):
        util.unregister_class_safe(cls)
