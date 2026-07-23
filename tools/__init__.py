from . import (
    analysis,
    annotations,
    enumeration,
    layout,
    reaction,
    shorthand,
    specialty_objects,
    state,
    stereo,
    stoichiometry,
    structure,
    style,
)

_MODULES = (structure, state, analysis, stereo, shorthand, layout, style,
            reaction, enumeration, annotations, specialty_objects,
            stoichiometry)


def register_all(mcp, bridge):
    for mod in _MODULES:
        mod.register(mcp, bridge)
