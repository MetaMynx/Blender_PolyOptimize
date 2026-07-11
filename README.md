# PolyOptimize

A Blender add-on that optimizes auto-generated 3D assets (AI-generated
models, SketchUp Make and Shapr3D exports, …) by merging coplanar faces
into single planes and optionally halving vertex counts per reduction
level.

## Installation

**Blender 4.2+ / 5.x (recommended):**

1. Zip the `poly_optimize` folder (the zip must contain
   `blender_manifest.toml` at its top level inside the folder).
2. In Blender: `Edit > Preferences > Add-ons > ⌄ (top-right) > Install
   from Disk…` and pick the zip.
3. Enable **PolyOptimize** if it isn't enabled automatically.

## Where to find it

The panel appears in **two places** (shared settings):

- **Properties editor > Modifier tab** (wrench icon) — below the modifier
  stack, when a mesh object is active.
- **3D Viewport > Sidebar (press N) > PolyOptimize tab.**
- **Add Modifier > Edit > PolyOptimize** — runs the optimizer immediately
  with the current panel settings. (Python add-ons can't register native
  stack modifiers, so this is a shortcut entry, not a live modifier.)

Settings do **not** live-preview like a modifier — they take effect when
you click **Optimize Polygons**. The button works in Object Mode and Edit Mode.

**Edit Mode = partial optimization.** Select any part of the mesh
(vertices, edges, or faces), and the optimizer processes only that
selection, applied directly to the model (Ctrl+Z to revert — the Result
dropdown is ignored in Edit Mode). Detail reduction is rescaled so
"Detail Kept" applies to the selected region rather than the whole mesh,
and the border between the selection and the rest of the mesh is never
broken.

## How it works

The pipeline runs in this order:

1. **Weld** — merges duplicate vertices. Auto-generated meshes often have
   split vertices along borders that would otherwise block face merging.
2. **Detail reduction** (optional) — collapse decimation down to the
   "Detail Kept" percentage (100% disables it, 50% keeps roughly half
   the triangles).
3. **Planar merge** — the core step. Connected faces whose normals agree
   within the **Planar Tolerance** angle are treated as one plane and
   merged into a single n-gon. Interior edges and vertices are removed;
   boundary vertices still used by non-coplanar neighboring faces are
   preserved, so shared borders between the model's sides stay
   watertight.
4. **Cleanup** — degenerate leftovers are removed; optionally the result
   is re-triangulated for game engines.

## Basic / Advanced labels

A toggle at the top of the panel switches all labels between
plain-language wording (**Basic**, the default — e.g. "Flatness
Sensitivity", "Protect Texture Seams") and standard Blender terminology
(**Advanced** — e.g. "Planar Tolerance", "Preserve UV Seams"). Tooltips
always show both: the plain explanation first, the technical term in
parentheses.

## Settings

| Setting | Effect |
| --- | --- |
| Output | Copy + hide original / copy offset beside it / copy overlapping / in-place (undo to revert) |
| Planar Tolerance | Max normal angle for faces to count as one plane (start at 1–5°) |
| Detail Kept | Percentage of detail to keep before flattening; 100% = off, 50% keeps roughly half the triangles |
| Simplify Region Boundaries | Also straightens merged-region outlines — more reduction, but can open gaps; leave off for watertight output |
| Triangulate Result | Convert merged n-gons back to triangles |
| Weld Duplicate Vertices / Distance | Pre-merge coincident vertices |
| Regenerate UVs | Rebuild the whole model’s UV map afterwards (Smart UV Project; creates one if missing) — always the full mesh, even for selection edits, so islands never overlap. Textures painted for the old layout will no longer line up |
| Rebuild UVs Only (button) | Regenerate the whole UV layout without optimizing any geometry |
| Preserve Seams / Sharp / Materials / UV Borders | Never merge faces across these boundaries (protects UVs and shading) |

The panel shows live counts for the active object and a before/after
summary of the last run.

## Notes and limitations

- In **Object Mode** it processes whole selected objects; in **Edit Mode** it processes the selected part of the active mesh, in place.
- Vertex reduction is skipped (with a warning) on meshes with **shape
  keys**, since collapse decimation would discard them.
- In *Copy — Offset* mode the **optimized copy** is placed beside the
  original on the X axis; the gap between them is configurable in the
  panel (appears when that mode is selected).
- Aggressive planar tolerance (>10°) can flatten intentional curvature;
  preview with a copy mode before using In-Place.

## Project layout

```
poly_optimize/
├── __init__.py             # 
