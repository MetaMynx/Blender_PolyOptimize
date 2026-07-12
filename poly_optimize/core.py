"""Mesh-optimization engine for PolyOptimize.

Pure geometry logic, deliberately isolated from UI and operator code so it
can be reviewed and unit-tested independently. Nothing in this module reads
``bpy.context``; every dependency is passed in explicitly.

Pipeline (in order):

1. **Weld** — merge vertices closer than a threshold. Auto-generated meshes
   (AI generators, SketchUp exports) frequently contain duplicated vertices
   along face borders; welding first lets every later stage see true
   connectivity.
2. **Collapse decimation** — optional coarse reduction to a fraction of the
   original detail (``detail_ratio``; 1.0 disables it). Runs *before* the
   planar pass so the coplanar triangles it produces are cleaned up next.
3. **Planar dissolve** — the core step: connected faces whose normals agree
   within an angle tolerance are merged into a single n-gon. Interior edges
   and vertices disappear, while boundary vertices shared with non-coplanar
   neighbours are preserved, keeping the mesh watertight.
4. **Degenerate cleanup** and optional re-triangulation for engines that
   prefer triangle output.

When ``only_selected`` is set (Edit-Mode invocations), every stage is
confined to the selected part of the mesh: weld and dissolve receive only
selected elements, and decimation is restricted through a temporary vertex
group with its ratio rescaled to the selection.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import bmesh
import bpy

# Vertices/edges below this size are considered degenerate leftovers.
_DEGENERATE_EPSILON = 1e-6
# Names are namespaced to avoid clashing with user data.
_DECIMATE_MODIFIER_NAME = "__poly_optimize_decimate"
_SELECTION_GROUP_NAME = "__poly_optimize_selection"


@dataclass(frozen=True)
class MeshStats:
    """Immutable snapshot of mesh element counts."""

    vertices: int
    edges: int
    faces: int

    @classmethod
    def of(cls, mesh: bpy.types.Mesh) -> "MeshStats":
        return cls(len(mesh.vertices), len(mesh.edges), len(mesh.polygons))


@dataclass(frozen=True)
class OptimizeParams:
    """Plain-data parameters, decoupled from Blender's PropertyGroup."""

    angle_limit: float  # radians
    weld_distance: float  # 0 disables welding
    detail_ratio: float  # fraction of detail kept; 1.0 disables decimation
    delimit: frozenset[str]  # bmesh dissolve_limit delimit flags
    simplify_boundaries: bool
    triangulate: bool
    only_selected: bool = False  # confine all stages to the selection


def optimize_object(
    obj: bpy.types.Object,
    params: OptimizeParams,
    depsgraph: bpy.types.Depsgraph,
) -> tuple[MeshStats, MeshStats, list[str]]:
    """Run the full pipeline on *obj*'s mesh in place.

    Returns ``(stats_before, stats_after, warnings)``.
    """
    mesh = obj.data
    before = MeshStats.of(mesh)
    warnings: list[str] = []

    if params.weld_distance > 0.0:
        _weld(mesh, params.weld_distance, params.only_selected)

    if params.detail_ratio < 1.0 - 1e-6:
        if mesh.shape_keys:
            warnings.append(
                f"'{obj.name}': detail reduction skipped (mesh has shape "
                "keys, which collapse decimation would discard)."
            )
        else:
            _apply_collapse_decimate(
                obj, params.detail_ratio, depsgraph, params.only_selected
            )
            mesh = obj.data  # decimation swaps the mesh datablock

    _dissolve_planar(
        mesh,
        angle_limit=params.angle_limit,
        delimit=set(params.delimit),
        simplify_boundaries=params.simplify_boundaries,
        triangulate=params.triangulate,
        only_selected=params.only_selected,
    )

    return before, MeshStats.of(mesh), warnings


def _weld(
    mesh: bpy.types.Mesh, distance: float, only_selected: bool
) -> None:
    """Merge vertices within *distance* of each other."""
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        verts = (
            [v for v in bm.verts if v.select] if only_selected else bm.verts
        )
        bmesh.ops.remove_doubles(bm, verts=verts, dist=distance)
        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.update()


def _dissolve_planar(
    mesh: bpy.types.Mesh,
    *,
    angle_limit: float,
    delimit: set[str],
    simplify_boundaries: bool,
    triangulate: bool,
    only_selected: bool,
) -> None:
    """Merge connected coplanar faces into single n-gons.

    ``dissolve_limit`` grows regions of faces whose normals agree within
    ``angle_limit`` and removes their interior edges, plus any two-edge
    vertices whose edges are collinear within the same tolerance. Vertices
    still referenced by faces outside a region are never removed, which is
    what keeps shared borders with other (possibly also-optimized) sides of
    the model intact.
    """

    def _scoped_edges(bm_: bmesh.types.BMesh) -> list:
        if not only_selected:
            return bm_.edges
        # Derive edge scope from vertex selection rather than edge flags:
        # robust even when flags haven't been flushed.
        return [
            e for e in bm_.edges
            if e.verts[0].select and e.verts[1].select
        ]

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        verts = (
            [v for v in bm.verts if v.select] if only_selected else bm.verts
        )
        bmesh.ops.dissolve_limit(
            bm,
            angle_limit=angle_limit,
            use_dissolve_boundaries=simplify_boundaries,
            verts=verts,
            edges=_scoped_edges(bm),
            delimit=delimit,
        )
        # Dissolving can occasionally leave zero-area/zero-length leftovers.
        # Recompute the edge scope: dissolve invalidated the previous list.
        bmesh.ops.dissolve_degenerate(
            bm, dist=_DEGENERATE_EPSILON, edges=_scoped_edges(bm)
        )
        if triangulate:
            faces = (
                [f for f in bm.faces if f.select]
                if only_selected
                else bm.faces
            )
            bmesh.ops.triangulate(bm, faces=faces)
        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.update()


def _apply_collapse_decimate(
    obj: bpy.types.Object,
    ratio: float,
    depsgraph: bpy.types.Depsgraph,
    only_selected: bool,
) -> None:
    """Apply a collapse-decimate at *ratio* and swap in the result.

    Uses a temporary Decimate modifier evaluated through the depsgraph
    rather than ``bpy.ops``, avoiding operator-context fragility. Other
    modifiers on the object are temporarily hidden so only the decimation
    is baked into the new mesh.

    With *only_selected*, the collapse is confined to the current vertex
    selection via a temporary vertex group, and the ratio is rescaled so
    the selected region — not the whole mesh — keeps ``ratio`` of its
    triangles. The selection is restored from the group afterwards, since
    decimation rebuilds topology and discards selection flags.
    """
    mesh = obj.data
    group = None
    if only_selected:
        ratio = _selection_scaled_ratio(mesh, ratio)
        # Clear any leftover from an interrupted earlier run first.
        stale = obj.vertex_groups.get(_SELECTION_GROUP_NAME)
        if stale is not None:
            obj.vertex_groups.remove(stale)
        group = obj.vertex_groups.new(name=_SELECTION_GROUP_NAME)
        group.add(
            [v.index for v in mesh.vertices if v.select], 1.0, "REPLACE"
        )

    suspended = [m for m in obj.modifiers if m.show_viewport]
    for mod in suspended:
        mod.show_viewport = False

    decimate = obj.modifiers.new(name=_DECIMATE_MODIFIER_NAME, type="DECIMATE")
    decimate.decimate_type = "COLLAPSE"
    decimate.ratio = ratio
    if group is not None:
        decimate.vertex_group = group.name

    try:
        depsgraph.update()
        evaluated = obj.evaluated_get(depsgraph)
        new_mesh = bpy.data.meshes.new_from_object(
            evaluated, preserve_all_data_layers=True, depsgraph=depsgraph
        )
    finally:
        obj.modifiers.remove(decimate)
        for mod in suspended:
            mod.show_viewport = True

    old_mesh = obj.data
    name = old_mesh.name
    obj.data = new_mesh
    if old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)
    new_mesh.name = name

    if group is not None:
        # ``obj.data`` was just swapped. Re-fetch the group by name: the
        # pre-swap reference is stale, because vertex-group storage
        # follows the mesh datablock (Blender 5.x) and the new mesh
        # carries its own copy of the group.
        fresh = obj.vertex_groups.get(_SELECTION_GROUP_NAME)
        if fresh is not None:
            _restore_selection_from_group(obj, fresh)
            obj.vertex_groups.remove(fresh)


def _selection_scaled_ratio(mesh: bpy.types.Mesh, ratio: float) -> float:
    """Rescale a per-selection ratio to Decimate's whole-mesh ratio.

    Decimate targets ``ratio`` of the *entire* mesh's triangles, but a
    vertex group confines which ones may collapse. To make the selected
    region keep ``ratio`` of its own triangles, solve
    ``sel * r + (total - sel) = total * r_eff`` for ``r_eff``.
    """
    total = sum(len(p.vertices) - 2 for p in mesh.polygons)
    selected = sum(len(p.vertices) - 2 for p in mesh.polygons if p.select)
    if total == 0 or selected == 0:
        return ratio
    return 1.0 - (selected / total) * (1.0 - ratio)


def _restore_selection_from_group(
    obj: bpy.types.Object, group: bpy.types.VertexGroup
) -> None:
    """Rebuild vertex/edge/face selection flags from *group* weights."""
    mesh = obj.data
    index = group.index
    selected = [
        any(g.group == index and g.weight > 0.5 for g in v.groups)
        for v in mesh.vertices
    ]
    for v, flag in zip(mesh.vertices, selected):
        v.select = flag
    for e in mesh.edges:
        a, b = e.vertices
        e.select = selected[a] and selected[b]
    for p in mesh.polygons:
        p.select = all(selected[i] for i in p.vertices)


def rebuild_uv_box_projection(
    mesh: bpy.types.Mesh, margin_fraction: float = 0.02
) -> bool:
    """Rebuild the active UV layer with an axis-aligned box projection.

    Implemented directly in bmesh — no operators, no mode switches, no
    context dependencies — because ``bpy.ops.uv.smart_project`` proved
    unreliable when invoked from panel code. Box projection suits the
    hard-surface assets this add-on targets: each face is projected
    along its dominant normal axis, connected same-axis faces form
    islands, and islands are shelf-packed into the 0–1 UV square with a
    margin, scaled for uniform texel density. A UV layer is created if
    none exists.

    Returns True when a layout was written (False for meshes without
    faces).
    """
    if not mesh.polygons:
        return False

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        uv_layer = bm.loops.layers.uv.active
        if uv_layer is None:
            uv_layer = bm.loops.layers.uv.new("UVMap")

        # Bucket faces by dominant normal axis: 0..5 = +X,-X,+Y,-Y,+Z,-Z.
        def bucket(face: bmesh.types.BMFace) -> int:
            n = face.normal
            axis = max(range(3), key=lambda i: abs(n[i]))
            return axis * 2 + (0 if n[axis] >= 0.0 else 1)

        buckets = {f: bucket(f) for f in bm.faces}

        # Grow islands: connected faces sharing the same bucket.
        seen: set = set()
        islands: list[tuple[int, list]] = []
        for face in bm.faces:
            if face in seen:
                continue
            group = buckets[face]
            stack, members = [face], []
            seen.add(face)
            while stack:
                current = stack.pop()
                members.append(current)
                for edge in current.edges:
                    for neighbour in edge.link_faces:
                        if neighbour not in seen and (
                            buckets[neighbour] == group
                        ):
                            seen.add(neighbour)
                            stack.append(neighbour)
            islands.append((group, members))

        # Project each island onto its axis plane, in object units so
        # every island shares one scale (uniform texel density).
        # Dominant axis -> the two coordinate indices used as (u, v).
        axis_uv = {0: (1, 2), 1: (0, 2), 2: (0, 1)}
        projected = []
        for group, members in islands:
            axis, flipped = group // 2, group % 2 == 1
            ui, vi = axis_uv[axis]
            sign = -1.0 if flipped else 1.0
            loops_uv = {}
            for face in members:
                for loop in face.loops:
                    co = loop.vert.co
                    loops_uv[loop] = (co[ui] * sign, co[vi])
            us = [uv[0] for uv in loops_uv.values()]
            vs = [uv[1] for uv in loops_uv.values()]
            projected.append(
                (loops_uv, min(us), min(vs),
                 max(us) - min(us), max(vs) - min(vs))
            )

        # Shelf-pack island bounding boxes into a roughly square atlas.
        total_area = sum(
            max(w, 1e-9) * max(h, 1e-9) for *_, w, h in projected
        )
        margin = margin_fraction * math.sqrt(total_area)
        target_width = math.sqrt(total_area) * 1.2
        projected.sort(key=lambda p: p[4], reverse=True)

        x = y = row_height = atlas_width = 0.0
        placements = []
        for loops_uv, min_u, min_v, width, height in projected:
            width = max(width, margin)
            height = max(height, margin)
            if x > 0.0 and x + width > target_width:
                x = 0.0
                y += row_height + margin
                row_height = 0.0
            placements.append((loops_uv, min_u, min_v, x, y))
            x += width + margin
            row_height = max(row_height, height)
            atlas_width = max(atlas_width, x)
        atlas_height = y + row_height

        # Normalise the atlas into the 0-1 square (uniform scale).
        scale = 1.0 / max(atlas_width, atlas_height, 1e-12)
        for loops_uv, min_u, min_v, offset_x, offset_y in placements:
            for loop, (u, v) in loops_uv.items():
                loop[uv_layer].uv = (
                    (offset_x + u - min_u) * scale,
                    (offset_y + v - min_v) * scale,
                )

        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.update()
    return True
