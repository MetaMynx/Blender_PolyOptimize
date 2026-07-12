"""PolyOptimize — merge coplanar faces and reduce vertex counts on
auto-generated 3D assets (AI generators, SketchUp, Shapr3D, …).

Installable two ways:
- As an *extension* (Blender 4.2+/5.x): the metadata lives in
  ``blender_manifest.toml``.
- As a *legacy add-on*: the ``bl_info`` block below is used instead.
"""

from __future__ import annotations

bl_info = {
    "name": "PolyOptimize",
    "author": "Aria Cheng",
    "version": (1, 8, 1),
    "blender": (4, 2, 0),
    "location": "Properties > Modifiers and View3D > Sidebar > PolyOptimize",
    "description": (
        "Merge coplanar faces into single planes and halve vertex counts "
        "per reduction level — for optimizing auto-generated 3D assets"
    ),
    "category": "Mesh",
}

from . import operators, panels, properties

_MODULES = (properties, operators, panels)


def register() -> None:
    registered = []
    try:
        for module in _MODULES:
            module.register()
            registered.append(module)
    except Exception:
        # Roll back so a failed enable never leaves half-registered
        # classes that would block the next attempt.
        for module in reversed(registered):
            try:
                module.unregister()
            except Exception:
                pass
        raise


def unregister() -> None:
    for module in reversed(_MODULES):
        try:
            module.unregister()
        except Exception:
            # Keep unregistering the rest even if one module was never
            # (or is no longer) registered.
            pass
