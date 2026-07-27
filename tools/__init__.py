from . import (
    analysis,
    annotations,
    enumeration,
    layout,
    naming,
    offline_parse,
    procedure_characterization,
    procedure_qc,
    procedure_reaction,
    procedure_scope,
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
            stoichiometry, procedure_reaction, procedure_scope,
            procedure_characterization, procedure_qc, naming, offline_parse)


def register_all(mcp, bridge):
    for mod in _MODULES:
        mod.register(mcp, bridge)
