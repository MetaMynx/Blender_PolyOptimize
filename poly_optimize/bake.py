"""Texture re-baking for PolyOptimize.

Rebuilding a UV layout orphans any texture authored for the old layout.
The fix implemented here is the standard studio workflow:

1. Keep the old UV map in place as the *render* map, so the object's
   materials keep sampling their textures exactly as before.
2. Generate the new box-projection layout into a second UV map.
3. Bake (render) the material's appearance into fresh images whose
   pixels are laid out for the new map.
4. Rewire the object's materials to a minimal PBR tree showing the baked
   images, and drop the old map.

A full PBR set is baked, but each pass only when the object actually
uses it (AI-generated assets commonly ship colour + roughness +
metallic + normal maps; SketchUp assets usually just colour):

- **colour** — always (Cycles diffuse pass, colour only).
- **roughness / normal** — when a material links textures into those
  inputs. The normal bake captures bump and normal-map detail in
  tangent space of the new layout.
- **metallic** — when linked; Blender has no metallic bake type, so the
  metallic input is temporarily routed through an emission shader and
  baked with the EMIT pass.

Unlinked scalar inputs (e.g. plain "roughness 0.4") are copied onto the
rewired material instead of baking an image. Shared materials are
copied before rewiring so other objects that use them are unaffected.
On any failure the object is left untouched: temporary UV map, images
and node rigs are removed and nothing is rewired.
"""

from __future__ import annotations

import bpy

from . import core

_BAKE_LAYER = "__poly_optimize_new_uvs"
_TEMP_NODE = "__poly_optimize_bake_target"
_TEMP_EMIT = "__poly_optimize_metallic_emit"

# Pass name -> (bake operator kwargs, image colour space, float buffer)
_PASS_CONFIG = {
    "color": (
        {"type": "DIFFUSE", "pass_filter": {"COLOR"}}, "sRGB", False
    ),
    "roughness": ({"type": "ROUGHNESS"}, "Non-Color", False),
    "metallic": ({"type": "EMIT"}, "Non-Color", False),
    "normal": ({"type": "NORMAL"}, "Non-Color", True),
}
# Bake order: metallic last among colour-like passes so its emission
# rig never overlaps another pass; normal needs no rig at all.
_PASS_ORDER = ("color", "roughness", "metallic", "normal")


def has_image_textures(obj: bpy.types.Object) -> bool:
    """True if any of *obj*'s materials samples an image texture."""
    for material in _node_materials(obj):
        for node in material.node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image is not None:
                return True
    return False


def bake_to_new_layout(
    context: bpy.types.Context,
    obj: bpy.types.Object,
    resolution: int,
    report,
) -> bool:
    """Rebuild *obj*'s UV layout and bake its PBR appearance to match.

    Returns True on success; on failure reports a warning and restores
    the object to its previous state.
    """
    mesh = obj.data
    stale = mesh.uv_layers.get(_BAKE_LAYER)
    if stale is not None:
        mesh.uv_layers.remove(stale)

    old_layer = mesh.uv_layers.active
    new_layer = mesh.uv_layers.new(name=_BAKE_LAYER)
    if new_layer is None:
        report(
            {"WARNING"},
            f"'{obj.name}': cannot add a UV map (limit reached) — "
            "textures were not baked",
        )
        return False
    core.rebuild_uv_box_projection(mesh, layer_name=_BAKE_LAYER)

    # Shaders sample the *render* map by default; the bake writes through
    # the *active* (selected) map. Point them at old and new respectively.
    old_layer.active_render = True
    mesh.uv_layers.active = mesh.uv_layers[_BAKE_LAYER]

    materials = _node_materials(obj)
    scalars = {m.name: _capture_scalars(m) for m in materials}
    passes = _needed_passes(materials)

    # Every material needs an active image-texture node holding the bake
    # target image; remember previous active nodes to restore afterwards.
    temp_nodes: list[tuple[bpy.types.Material, bpy.types.Node]] = []
    previous_active: list[tuple[bpy.types.Material, bpy.types.Node]] = []
    for material in materials:
        nodes = material.node_tree.nodes
        previous_active.append((material, nodes.active))
        node = nodes.new("ShaderNodeTexImage")
        node.name = _TEMP_NODE
        node.select = True
        nodes.active = node
        temp_nodes.append((material, node))

    images: dict[str, bpy.types.Image] = {}
    try:
        for pass_name in _PASS_ORDER:
            if pass_name not in passes:
                continue
            bake_kwargs, colorspace, float_buffer = _PASS_CONFIG[pass_name]
            image = bpy.data.images.new(
                name=f"{obj.name}_baked_{pass_name}",
                width=resolution,
                height=resolution,
                float_buffer=float_buffer,
            )
            image.colorspace_settings.name = colorspace
            images[pass_name] = image
            for _, node in temp_nodes:
                node.image = image

            emission_rigs = []
            if pass_name == "metallic":
                emission_rigs = [
                    _rig_metallic_emission(m) for m in materials
                ]
            try:
                _run_bake(context, obj, resolution, bake_kwargs)
            finally:
                for material, rig in zip(materials, emission_rigs):
                    _unrig_metallic_emission(material, rig)
    except (RuntimeError, TypeError) as error:
        report({"WARNING"}, f"'{obj.name}': texture bake failed — {error}")
        _restore(mesh, images, temp_nodes, previous_active)
        return False

    for material, node in temp_nodes:
        material.node_tree.nodes.remove(node)
    for material, node in previous_active:
        if node is not None:
            material.node_tree.nodes.active = node

    for image in images.values():
        image.pack()

    # Rewire materials to show the baked maps; copy shared ones first so
    # other objects using them keep their original look.
    for slot in obj.material_slots:
        material = slot.material
        if material is None or not material.use_nodes:
            continue
        original_name = material.name
        if material.users > 1:
            material = material.copy()
            material.name = f"{original_name}_baked"
            slot.material = material
        _rewire_material(material, images, scalars.get(original_name, {}))

    # Promote the new layout, drop the old ones.
    for layer in [l for l in mesh.uv_layers if l.name != _BAKE_LAYER]:
        mesh.uv_layers.remove(layer)
    layer = mesh.uv_layers[_BAKE_LAYER]
    layer.name = "UVMap"
    layer.active_render = True
    mesh.uv_layers.active = layer

    baked = ", ".join(p for p in _PASS_ORDER if p in images)
    report({"INFO"}, f"'{obj.name}': baked {baked} to the new layout")
    return True


def _node_materials(obj: bpy.types.Object) -> list[bpy.types.Material]:
    return [
        slot.material
        for slot in obj.material_slots
        if slot.material is not None and slot.material.use_nodes
    ]


def _first_principled(
    material: bpy.types.Material,
) -> bpy.types.Node | None:
    for node in material.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            return node
    return None


def _needed_passes(materials: list[bpy.types.Material]) -> set[str]:
    """Colour always; other passes only when a material links that input."""
    passes = {"color"}
    for material in materials:
        principled = _first_principled(material)
        if principled is None:
            continue
        for pass_name, socket in (
            ("roughness", "Roughness"),
            ("metallic", "Metallic"),
            ("normal", "Normal"),
        ):
            if principled.inputs[socket].is_linked:
                passes.add(pass_name)
    return passes


def _capture_scalars(material: bpy.types.Material) -> dict:
    """Remember unlinked scalar inputs so the rewire can copy them."""
    principled = _first_principled(material)
    if principled is None:
        return {}
    values = {}
    for key, socket in (("roughness", "Roughness"), ("metallic", "Metallic")):
        if not principled.inputs[socket].is_linked:
            values[key] = principled.inputs[socket].default_value
    return values


def _rig_metallic_emission(material: bpy.types.Material):
    """Route the metallic input through an emission shader for EMIT bake.

    Returns (emission node, original from_socket, output socket) so the
    rig can be undone exactly.
    """
    tree = material.node_tree
    output = tree.get_output_node("CYCLES")
    if output is None:
        return None
    surface = output.inputs["Surface"]
    original = surface.links[0].from_socket if surface.is_linked else None

    emission = tree.nodes.new("ShaderNodeEmission")
    emission.name = _TEMP_EMIT
    principled = _first_principled(material)
    if principled is not None:
        metallic = principled.inputs["Metallic"]
        if metallic.is_linked:
            tree.links.new(
                metallic.links[0].from_socket, emission.inputs["Color"]
            )
        else:
            value = metallic.default_value
            emission.inputs["Color"].default_value = (
                value, value, value, 1.0
            )
    else:
        emission.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    tree.links.new(emission.outputs["Emission"], surface)
    return (emission, original, surface)


def _unrig_metallic_emission(material: bpy.types.Material, rig) -> None:
    if rig is None:
        return
    emission, original, surface = rig
    tree = material.node_tree
    tree.nodes.remove(emission)
    if original is not None:
        tree.links.new(original, surface)


def _run_bake(
    context: bpy.types.Context,
    obj: bpy.types.Object,
    resolution: int,
    bake_kwargs: dict,
) -> None:
    """Run one Cycles bake pass on *obj*, restoring scene state."""
    scene = context.scene
    view_layer = context.view_layer
    previous_engine = scene.render.engine
    previous_selection = list(context.selected_objects)
    previous_active = view_layer.objects.active
    previous_samples = None

    scene.render.engine = "CYCLES"
    if hasattr(scene, "cycles"):
        previous_samples = scene.cycles.samples
        # These passes are noise-free at low sample counts; keep it fast.
        scene.cycles.samples = 4

    for other in previous_selection:
        other.select_set(False)
    obj.select_set(True)
    view_layer.objects.active = obj

    try:
        bpy.ops.object.bake(
            margin=max(4, resolution // 256),
            use_clear=True,
            **bake_kwargs,
        )
    finally:
        scene.render.engine = previous_engine
        if previous_samples is not None:
            scene.cycles.samples = previous_samples
        obj.select_set(False)
        for other in previous_selection:
            try:
                other.select_set(True)
            except RuntimeError:
                pass
        view_layer.objects.active = previous_active


def _rewire_material(
    material: bpy.types.Material,
    images: dict[str, bpy.types.Image],
    scalars: dict,
) -> None:
    """Replace the node tree with a minimal baked-PBR shader."""
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.location = (400, 0)
    bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (0, 0)
    tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    def add_texture(pass_name: str, y: int) -> bpy.types.Node | None:
        image = images.get(pass_name)
        if image is None:
            return None
        node = tree.nodes.new("ShaderNodeTexImage")
        node.location = (-450, y)
        node.image = image
        node.label = pass_name.capitalize()
        return node

    color = add_texture("color", 300)
    if color is not None:
        tree.links.new(color.outputs["Color"], bsdf.inputs["Base Color"])

    roughness = add_texture("roughness", 0)
    if roughness is not None:
        tree.links.new(
            roughness.outputs["Color"], bsdf.inputs["Roughness"]
        )
    elif "roughness" in scalars:
        bsdf.inputs["Roughness"].default_value = scalars["roughness"]

    metallic = add_texture("metallic", -300)
    if metallic is not None:
        tree.links.new(metallic.outputs["Color"], bsdf.inputs["Metallic"])
    elif "metallic" in scalars:
        bsdf.inputs["Metallic"].default_value = scalars["metallic"]

    normal = add_texture("normal", -600)
    if normal is not None:
        normal_map = tree.nodes.new("ShaderNodeNormalMap")
        normal_map.location = (-200, -600)
        tree.links.new(normal.outputs["Color"], normal_map.inputs["Color"])
        tree.links.new(normal_map.outputs["Normal"], bsdf.inputs["Normal"])


def _restore(
    mesh: bpy.types.Mesh,
    images: dict[str, bpy.types.Image],
    temp_nodes: list,
    previous_active: list,
) -> None:
    """Undo all bake preparation after a failure."""
    for material, node in temp_nodes:
        try:
            material.node_tree.nodes.remove(node)
        except (RuntimeError, ReferenceError):
            pass
        # Remove any emission rig a failed metallic pass left behind.
        try:
            leftover = material.node_tree.nodes.get(_TEMP_EMIT)
            if leftover is not None:
                material.node_tree.nodes.remove(leftover)
        except (RuntimeError, ReferenceError):
            pass
    for material, node in previous_active:
        if node is not None:
            try:
                material.node_tree.nodes.active = node
            except (RuntimeError, ReferenceError):
                pass
    layer = mesh.uv_layers.get(_BAKE_LAYER)
    if layer is not None:
        mesh.uv_layers.remove(layer)
    for image in images.values():
        if image.users == 0:
            bpy.data.images.remove(image)
