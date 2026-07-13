"""Operators for PolyOptimize."""

from __future__ import annotations

import bpy

from . import bake, core, util

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

        if optimized and settings.regenerate_uvs:
            _rebuild_uvs(context, optimized, self.report)

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
            # Land beside the original regardless of its size: one full
            # width along X, plus the user-configured gap.
            copy.location.x += original.dimensions.x + settings.offset_gap
        # COPY_OVERLAP: nothing else to do — both stay in place.
        return copy


class OBJECT_OT_poly_optimize_rebuild_uvs(bpy.types.Operator):
    """Rebuild the whole texture layout (UV map) of the selected mesh
    objects without changing their geometry"""

    bl_idname = "object.poly_optimize_rebuild_uvs"
    bl_label = "Rebuild UVs Only"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if context.mode not in SUPPORTED_MODES:
            cls.poll_message_set("Requires Object Mode or Edit Mode")
            return False
        return True

    def execute(self, context: bpy.types.Context) -> set[str]:
        edit_mode = context.mode == "EDIT_MESH"
        if edit_mode:
            targets = [
                o for o in context.objects_in_mode if o.type == "MESH"
            ]
            bpy.ops.object.mode_set(mode="OBJECT")
        else:
            targets = [
                o for o in _target_objects(context) if o.type == "MESH"
            ]

        if not targets:
            self.report({"ERROR"}, "Select at least one mesh object")
            if edit_mode:
                bpy.ops.object.mode_set(mode="EDIT")
            return {"CANCELLED"}

        rebuilt = _rebuild_uvs(context, targets, self.report)
        if edit_mode:
            bpy.ops.object.mode_set(mode="EDIT")
        if not rebuilt:
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Rebuilt the UV layout of {rebuilt} of {len(targets)} "
            "object(s)",
        )
        return {"FINISHED"}


def _rebuild_uvs(
    context: bpy.types.Context,
    objects: list[bpy.types.Object],
    report,
) -> int:
    """Rebuild each object's whole UV layout via the core box projection.

    Always the entire mesh — re-unwrapping only a selection would stack
    its new islands on top of the existing layout. Pure mesh-data
    operation (see ``core.rebuild_uv_box_projection``): needs no mode
    switches and cannot silently no-op the way ``uv.smart_project`` did
    when invoked from panel code. Objects must be in Object Mode.
    Failures are reported per object; returns the rebuilt count.
    """
    settings = context.scene.poly_optimize
    rebuilt = 0
    for obj in objects:
        use_bake = settings.bake_textures and bake.bakeable(obj)
        try:
            if settings.bake_textures and not use_bake:
                report(
                    {"INFO"},
                    f"'{obj.name}': no materials — nothing to bake, "
                    "layout rebuilt only",
                )
            if use_bake:
                written = bake.bake_to_new_layout(
                    context, obj, int(settings.bake_resolution), report
                )
            else:
                written = core.rebuild_uv_box_projection(obj.data)
                if not written:
                    report(
                        {"WARNING"},
                        f"'{obj.name}': no faces to unwrap, "
                        "UV layout untouched",
                    )
        except Exception as error:  # noqa: BLE001 — report, don't crash
            report({"WARNING"}, f"'{obj.name}': UV rebuild failed — {error}")
            continue
        if written:
            rebuilt += 1
    return rebuilt


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


_CLASSES = (OBJECT_OT_poly_optimize, OBJECT_OT_poly_optimize_rebuild_uvs)


def register() -> None:
    for cls in _CLASSES:
        util.register_class_fresh(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        util.unregister_class_safe(cls)
