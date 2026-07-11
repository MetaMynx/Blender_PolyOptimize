"""Registration helpers for PolyOptimize.

Blender leaves classes registered if an add-on's ``register()`` fails
partway, or if two copies of the add-on are installed side by side
(e.g. a legacy add-on plus an extension). Both situations produce
"already registered as a subclass" errors on the next enable. These
helpers recover from stale registrations instead of failing.
"""

from __future__ import annotations

import bpy


def register_class_fresh(cls: type) -> None:
    """Register *cls*, first unregistering any stale class of the same name.

    Registered classes are exposed on ``bpy.types`` under their RNA
    identifier, which defaults to the class name — so a leftover from a
    previous copy of this add-on can be found and removed by name.
    """
    stale = getattr(bpy.types, cls.__name__, None)
    if stale is not None:
        try:
            bpy.utils.unregister_class(stale)
        except RuntimeError:
            pass
    bpy.utils.register_class(cls)


def unregister_class_safe(cls: type) -> None:
    """Unregister *cls*, ignoring 'not registered' errors."""
    try:
        bpy.utils.unregister_class(cls)
    except RuntimeError:
        pass
