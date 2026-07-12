# PolyOptimize
A Blender Add-On that optimizes generated 3D assets (AI-generated models, SketchUp Make, Shapr3D exports, etc.) by merging coplanar faces into single planes and optionally reducing detail to a chosen percentage, with optional ability to auto-generate the model's UV remap/rebuild with full-PBR texture baking. Simplification available for the whole model in Object Mode, and selected portions in Edit Mode.

*No more having to deal with "Millions of polys for a simple box".*

![[docs/polyOpti_img02-steppedSimplify.png]]![[docs/polyOpti_img03-EditMode.png]]

## Installation

**Blender 4.2+ / 5.x (recommended):**

1. Download the latest release build (currently v1.8.2).
2. In Blender:
	- `Edit > Preferences > Add-ons > v (top-right) > Install from Disk...` and pick the zip.
	*or:*
	- Drag-and-drop the zip directly into Blender, ensure `Enable Add-on` is Checked, and click `Ok`. 
	![[docs/polyOpti_img01-Install.png]]
3. Enable **PolyOptimize** if it isn't enabled automatically, or wasn't enabled when adding directly. This can be found in `Edit > Preferences > Add-ons`.

## Where to Find it

The panel appears in **two places** (shared settings):

- **Properties editor > Modifier tab** (wrench icon) - below the modifier stack, when a mesh object is active.
- **3D Viewport > Sidebar (press N) > PolyOptimize tab.**
- **Add Modifier > Edit > PolyOptimize** - runs the optimizer immediately with the current panel settings. (Python add-ons can't register native stack modifiers, so this is a shortcut entry, not a live modifier.)

Settings do **not** live-preview like a modifier - they take effect when you click **Optimize Polygons**. The button works in Object Mode and Edit Mode.

**Edit Mode = partial optimization.** Select any part of the mesh (vertices, edges, or faces), and the optimizer processes only that selection, applied directly to the model (Ctrl+Z to revert -- the Result dropdown is ignored in Edit Mode). Detail reduction is rescaled so "Detail Kept" applies to the selected region rather than the whole mesh, and the border between the selection and the rest of the mesh is never broken.

## How it Works

The pipeline runs in this order:

1. **Weld** : merges duplicate vertices.
   Auto-generated meshes often have split vertices along borders that would otherwise block face merging.
   
2. **Detail reduction** (optional).
   Collapse decimation down to the "Detail Kept" percentage (100% disables it, 50% keeps roughly half the triangles).
   
3. **Planar merge** : the core step.
   Connected faces whose normals agree within the **Planar Tolerance** angle are treated as one plane and merged into a single N-gon. Interior edges and vertices are removed; boundary vertices still used by non-coplanar neighboring faces are preserved, so shared borders between the model's sides stay watertight.
   
4. **Cleanup** : degenerate leftovers are removed.
   Optionally the result is re-triangulated for game engines.

## Basic / Advanced labels

A toggle at the top of the panel switches all labels between plain-language wording, and Tooltips always show in both - the plain explanation first, the technical term in parentheses:

### Basic Mode
The Default (Beginner-friendly) - e.g. "Flatness Sensitivity", "Protect Texture Seams".
![[docs/polyOpti_img04-labelBasic.png]]

### Advanced Mode
Standard Blender terminology - e.g. "Planar Tolerance", "Preserve UV Seams".
  ![[docs/polyOpti_img05-labelAdv.png]]

## Settings

| Setting                                         | Effect                                                                                                                                                                                                                                                                                                                                                                               |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Output                                          | Copy + hide original / copy offset beside it / copy overlapping / in-place (undo to revert)                                                                                                                                                                                                                                                                                          |
| Planar Tolerance                                | Max normal angle for faces to count as one plane (start at 1-5 degrees)                                                                                                                                                                                                                                                                                                              |
| Detail Kept                                     | Percentage of detail to keep before flattening; 100% = off, 50% keeps roughly half the triangles                                                                                                                                                                                                                                                                                     |
| Simplify Region Boundaries                      | Also straightens merged-region outlines — more reduction, but can open gaps; leave off for watertight output                                                                                                                                                                                                                                                                         |
| Triangulate Result                              | Convert merged n-gons back to triangles                                                                                                                                                                                                                                                                                                                                              |
| Weld Duplicate Vertices / Distance              | Pre-merge coincident vertices                                                                                                                                                                                                                                                                                                                                                        |
| Regenerate UVs                                  | Rebuild the whole model's UV map afterwards (axis-aligned box projection with packed islands; creates a map if missing) — always the full mesh, even for selection edits, so islands never overlap. Textures painted for the old layout will no longer line up unless baking is enabled                                                                                              |
| Rebuild UVs Only (button)                       | Regenerate the whole UV layout without optimizing any geometry                                                                                                                                                                                                                                                                                                                       |
| Bake Textures to New UVs                        | When the model already has image textures, bake them into new images matching the rebuilt layout, so it keeps looking the same. Bakes the full PBR set as needed — colour always; roughness, metallic and normal/bump maps when the materials use them (plain scalar values are copied). One packed image per map per object; materials are simplified to a standard textured shader |
| Bake Resolution                                 | Size of the baked images (512-4096 px)                                                                                                                                                                                                                                                                                                                                               |
| Preserve Seams / Sharp / Materials / UV Borders | Never merge faces across these boundaries (protects UVs and shading)                                                                                                                                                                                                                                                                                                                 |

The panel shows live counts for the active object and a before/after summary of the last run.

## Notes & Limitations

- In **Object Mode** : it processes whole selected objects; in **Edit Mode** it processes the selected part of the active mesh, in place.
- Detail reduction is skipped (with a warning) on meshes with **shape keys**, since collapse decimation would discard them.
- In ***Copy - Offset* mode** : the **optimized copy** is placed beside the original on the X axis; the gap between them is configurable in the panel (appears when that mode is selected).
- In **Edit Mode** : all edits Occur In-Place, regardless of the Output drop-down selection.
- Aggressive Flatness Sensitivity / Planar Tolerance (>10 degrees) can flatten intentional curvature; preview with a copy mode before using In-Place.
- Texture baking runs Cycles renders and briefly freezes the UI; only surface colour, roughness, metallic and normals are carried over (not emission or transparency maps).
- Box-projection UV islands on faces pointing in negative axis directions are mirrored in UV space. The bake compensates, so models render correctly - it only matters if you hand-paint the baked images later.

## Project Layout

```
poly_optimize/
|-- __init__.py             # registration + legacy bl_info
|-- blender_manifest.toml   # extension metadata (Blender 4.2+)
|-- bake.py                 # PBR texture re-baking onto rebuilt UVs
|-- core.py                 # pure mesh-optimization engine (no UI)
|-- operators.py            # Optimize + Rebuild UVs operators
|-- panels.py               # UI, registered in both locations
|-- properties.py           # shared Scene-level settings
`-- util.py                 # fault-tolerant class registration helpers
```

## License

GPL-3.0-or-later (required for Blender add-ons).

## Author Notes
I am an Architecture student that is new to learning Blender. My primary use case for this is importing all my work I've done in SketchUp. In that process, I discovered that SketchUp creates far too many polys. While I know how to rebuild it myself in Blender (painstakingly, with my current skill level), I recognize that in the long run - regardless of my skill - it will take me forever.

I've also read about indie game developers that are trying to work with AI-generated assets that produce "millions of polys for a simple box", and didn't want to deal with having to remap the UV even after using a Remesh modifier to reduce the poly count. Totally understandable.

I sought to solve this with a plugin that does exactly what I want: it's a fairly basic, very focused plugin, as I still don't know much about Blender, so I am able to limit the scope to *basic functionality*, while still solving a ton of problems for a lot of people (not just myself).

-

Despite its minor quirks (please see *Notes & Limitations*)... Everything works as I've tested everything as much as I could (within the realm of my limited Blender knowledge), and I'm very happy with what Fable5 produced.

The idea for the *Method of Simplification* is mine, but Claude articulated it better into existing concepts, and built it based on what I prompted. I tested and did a fair amount of QA testing to get it from the init commit to its current build state.

I hope this plugin is useful for you as well, and lets you focus on the *real creative work* that you want to focus on, instead of feeling forced to do this *gross* process manually.
