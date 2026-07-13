from . import (
    analysis,
    enumeration,
    layout,
    reaction,
    shorthand,
    state,
    stereo,
    structure,
    style,
)

_MODULES = (structure, state, analysis, stereo, shorthand, layout, style,
            reaction, enumeration)


def register_all(mcp, bridge):
    for mod in _MODULES:
        mod.register(mcp, bridge)
