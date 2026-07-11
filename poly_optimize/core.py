"""Mesh-optimization engine for PolyOptimize.

Pure geometry logic, deliberately isolated from UI and operator code so it
can be reviewed and unit-tested independently. Nothing in this module reads
``bpy.context``; every dependency is passed in explicitly.

Pipeline (in order):

1. **Weld** — merge vertices closer than a threshold. Auto-generated meshes
   (AI generators, SketchUp exports) frequently contain duplicated vertices
   along face borders; welding first lets every later stage see true
   connectivity.
2. **Collapse decimation** — optional coarse reduction. Each *level* halves
   the remaining vertex budget (``ratio = 0.5 ** level``). Runs *before* the
   planar pass so the coplanar triangles it produces are cleaned up next.
3. **Planar dissolve** — the core step: connected faces whose normals agree
   within an angle tolerance are merged into a single n-gon. Interior edges
   and vertices disappear, while boundary vertices shared with non-coplanar
   neighbours are preserved, keeping the mesh watertight.
4. **Degenerate cleanup** and optional re-triangulation for engines that
   prefer triangle output.
"""

from __future__ import annotations

from dataclasses import dataclass

import bmesh
import bpy

# Vertices/edges below this size are considered degenerate leftovers.
_DEGENERATE_EPSILON = 1e-6
# Modifier name is namespaced to avoid clashing with user modifiers.
_DECIMATE_MODIFIER_NAME = "__poly_optimize_decimate"


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
    reduction_level: int  # 0 disables decimation; each level halves vertices
    delimit: frozenset[str]  # bmesh dissolve_limit delimit flags
    simplify_boundaries: bool
    triangulate: bool

    @property
    def decimate_ratio(self) -> float:
        return 0.5 ** self.reduction_level


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
        _weld(mesh, params.weld_distance)

    if params.reduction_level > 0:
        if mesh.shape_keys:
            warnings.append(
                f"'{obj.name}': vertex reduction skipped (mesh has shape "
                "keys, which collapse decimation would discard)."
            )
        else:
            _apply_collapse_decimate(obj, params.decimate_ratio, depsgraph)
            mesh = obj.data  # decimation swaps the mesh datablock

    _dissolve_planar(
        mesh,
        angle_limit=params.angle_limit,
        delimit=set(params.delimit),
        simplify_boundaries=params.simplify_boundaries,
        triangulate=params.triangulate,
    )

    return before, MeshStats.of(mesh), warnings


def _weld(mesh: bpy.types.Mesh, distance: float) -> None:
    """Merge vertices within *distance* of each other."""
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=distance)
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
) -> None:
    """Merge connected coplanar faces into single n-gons.

    ``dissolve_limit`` grows regions of faces whose normals agree within
    ``angle_limit`` and removes their interior edges, plus any two-edge
    vertices whose edges are collinear within the same tolerance. Vertices
    still referenced by faces outside a region are never removed, which is
    what keeps shared borders with other (possibly also-optimized) sides of
    the model intact.
    """
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bmesh.ops.dissolve_limit(
            bm,
            angle_limit=angle_limit,
            use_dissolve_boundaries=simplify_boundaries,
            verts=bm.verts,
            edges=bm.edges,
            delimit=delimit,
        )
        # Dissolving can occasionally leave zero-area/zero-length leftovers.
        bmesh.ops.dissolve_degenerate(
            bm, dist=_DEGENERATE_EPSILON, edges=bm.edges
        )
        if triangulate:
            bmesh.ops.triangulate(bm, faces=bm.faces)
        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.update()


def _apply_collapse_decimate(
    obj: bpy.types.Object,
    ratio: float,
    depsgraph: bpy.types.Depsgraph,
) -> None:
    """Apply a collapse-decimate at *ratio* and swap in the result.

    Uses a temporary Decimate modifier evaluated through the depsgraph
    rather than ``bpy.ops``, avoiding operator-context fragility. Other
    modifiers on the object are temporarily hidden so only the decimation
    is baked into the new mesh.
    """
    suspended = [m for m in obj.modifiers if m.show_viewport]
    for mod in suspended:
        mod.show_viewport = False

    decimate = obj.modifiers.new(name=_DECIMATE_MODIFIER_NAME, type="DECIMATE")
    decimate.decimate_type = "COLLAPSE"
    decimate.ratio = ratio

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
