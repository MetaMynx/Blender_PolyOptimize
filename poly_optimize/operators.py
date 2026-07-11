"""Operators for PolyOptimize."""

from __future__ import annotations

import bpy

from . import core, util

# Gap between the original and an offset copy, as a fraction of width.
_OFFSET_MARGIN = 1.2
# Modes the operator can run from. In Edit Mode the optimization is
# confined to the selection and applied in place.
SUPPORTED_MODES = {"OBJECT", "EDIT_MESH"}


class OBJECT_OT_poly_optimize(bpy.types.Operator):
    """Merge coplanar faces and optionally reduce detail. In Object Mode
    this processes whole selected objects; in Edit Mode it processes only
    the selected part of the mesh, in place"""

    bl_idname = "object.poly_optimize"
    bl_label = "Optimize Polygons"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if context.mode not in SUPPORTED_MODES:
            cls.poll_message_set("Requires Object Mode or Edit Mode")
            return False
        if context.mode == "OBJECT" and not any(
            o.type == "MESH" for o in _target_objects(context)
        ):
            cls.poll_message_set("Select at least one mesh object")
            return False
        return True

    def execute(self, context: bpy.types.Context) -> set[str]:
        settings = context.scene.poly_optimize
        edit_mode = context.mode == "EDIT_MESH"

        if edit_mode:
            targets = self._edit_mode_targets(context)
            if not targets:
                self.report(
                    {"ERROR"},
                    "Nothing is selected — select part of the mesh first",
                )
                bpy.ops.object.mode_set(mode="EDIT")
                return {"CANCELLED"}
            if settings.output_mode != "IN_PLACE":
                self.report(
                    {"INFO"},
                    "Edit Mode: changes the selection directly "
                    "(Ctrl+Z to revert); the Result setting is ignored",
                )
        else:
            targets = [
                o for o in _target_objects(context) if o.type == "MESH"
            ]

        params = core.OptimizeParams(
            angle_limit=settings.angle_limit,
            weld_distance=(
                settings.weld_distance if settings.use_weld else 0.0
            ),
            detail_ratio=settings.detail_percent / 100.0,
            delimit=settings.delimit_flags(),
            simplify_boundaries=settings.simplify_boundaries,
            triangulate=settings.triangulate,
            only_selected=edit_mode,
        )
        depsgraph = context.evaluated_depsgraph_get()

        totals_before = totals_after = core.MeshStats(0, 0, 0)
        optimized: list[bpy.types.Object] = []

        for original in targets:
            work = (
                original
                if edit_mode
                else self._prepare_target(context, original, settings)
            )
            try:
                before, after, warnings = core.optimize_object(
                    work, params, depsgraph
                )
            except RuntimeError as error:
                self.report({"ERROR"}, f"'{original.name}': {error}")
                continue
            for message in warnings:
                self.report({"WARNING"}, message)
            totals_before = _add_stats(totals_before, before)
            totals_after = _add_stats(totals_after, after)
            optimized.append(work)

        if edit_mode:
            # Return to where the user was, success or not.
            bpy.ops.object.mode_set(mode="EDIT")
        elif optimized:
            _select_only(context, optimized)

        if not optimized:
            return {"CANCELLED"}

        _store_stats(settings, totals_before, totals_after)
        self.report({"INFO"}, _summary(totals_before, totals_after))
        return {"FINISHED"}

    @staticmethod
    def _edit_mode_targets(
        context: bpy.types.Context,
    ) -> list[bpy.types.Object]:
        """Leave Edit Mode and return the edited meshes with a selection.

        Selection flags on ``Mesh`` data are only synced back when leaving
        Edit Mode, so the mode switch must happen before reading them.
        """
        candidates = [
            o for o in context.objects_in_mode if o.type == "MESH"
        ]
        bpy.ops.object.mode_set(mode="OBJECT")
        return [
            o for o in candidates
            if any(v.select for v in o.data.vertices)
        ]

    def _prepare_target(
        self,
        context: bpy.types.Context,
        original: bpy.types.Object,
        settings: "bpy.types.PropertyGroup",
    ) -> bpy.types.Object:
        """Return the object the pipeline should modify, per output mode."""
        mode = settings.output_mode
        if mode == "IN_PLACE":
            return original

        copy = original.copy()
        copy.data = original.data.copy()
        copy.name = f"{original.name}_optimized"
        collections = original.users_collection or (
            context.scene.collection,
        )
        for collection in collections:
            collection.objects.link(copy)

        if mode == "COPY_HIDE":
            original.hide_set(True)
            original.hide_render = True
        elif mode == "COPY_OFFSET":
            copy.location.x += max(original.dimensions.x, 1.0) * _OFFSET_MARGIN
        # COPY_OVERLAP: nothing else to do — both stay in place.
        return copy


def _target_objects(context: bpy.types.Context) -> list[bpy.types.Object]:
    """Selected objects, falling back to the active object."""
    if context.selected_objects:
        return list(context.selected_objects)
    return [context.active_object] if context.active_object else []


def _select_only(
    context: bpy.types.Context, objects: list[bpy.types.Object]
) -> None:
    for obj in context.selected_objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    context.view_layer.objects.active = objects[-1]


def _add_stats(a: core.MeshStats, b: core.MeshStats) -> core.MeshStats:
    return core.MeshStats(
        a.vertices + b.vertices, a.edges + b.edges, a.faces + b.faces
    )


def _store_stats(
    settings: "bpy.types.PropertyGroup",
    before: core.MeshStats,
    after: core.MeshStats,
) -> None:
    settings.has_result = True
    settings.last_verts_before = before.vertices
    settings.last_verts_after = after.vertices
    settings.last_edges_before = before.edges
    settings.last_edges_after = after.edges
    settings.last_faces_before = before.faces
    settings.last_faces_after = after.faces


def _summary(before: core.MeshStats, after: core.MeshStats) -> str:
    saved = before.faces - after.faces
    percent = (saved / before.faces * 100.0) if before.faces else 0.0
    return (
        f"Faces {before.faces:,} -> {after.faces:,} "
        f"({percent:.1f}% removed), "
        f"verts {before.vertices:,} -> {after.vertices:,}"
    )


def register() -> None:
    util.register_class_fresh(OBJECT_OT_poly_optimize)


def unregister() -> None:
    util.unregister_class_safe(OBJECT_OT_poly_optimize)
