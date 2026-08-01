"""ChemDrawBridge: every tool call funnels through here.

Public methods are thread-safe: each builds a closure and submits it to the
single COM worker thread. Only plain Python data (str/int/dict/bytes) crosses
the boundary — COM objects never leave the worker thread.

This class is composed from per-concern mixins (one file each, below) rather
than defined in one file — the original single-file version grew past 2,000
lines with ~90 methods covering unrelated concerns (document lifecycle,
layout, shorthand contraction, stereochemistry...). Every consumer
(tools/*.py, server.py, test_smoke_visual.py) calls bridge.<method_name>(...)
as a flat attribute access, so the split preserves that: mixin composition makes
every method directly callable on the instance regardless of which file
defines it, with no nesting and no changes needed anywhere outside this
package. No method name is duplicated across any two mixins (that's what
makes this safe) — see tests/ for a completeness check against this
invariant if one is ever added.
"""
from ._annotations import _Annotations
from ._document_session import _DocumentSession
from ._enumeration import _Enumeration
from ._highlight import _Highlight
from ._layout import _Layout
from ._manipulation import _Manipulation
from ._plumbing import _Plumbing
from ._properties_qc import _PropertiesQC
from ._reaction import _Reaction
from ._selection import _Selection
from ._shorthand import _Shorthand
from ._specialty_objects import _SpecialtyObjects
from ._state_diff import _StateDiff
from ._stereochemistry import _Stereochemistry
from ._stoichiometry import _Stoichiometry
from ._structure_io import _StructureIO
from ._style import _Style


class ChemDrawBridge(_Plumbing, _DocumentSession, _Selection, _StateDiff,
                     _StructureIO, _PropertiesQC, _Manipulation,
                     _Stereochemistry, _Shorthand, _Layout, _Style,
                     _Reaction, _Enumeration, _Annotations, _SpecialtyObjects,
                     _Stoichiometry, _Highlight):
    """See the module docstring above. Only _Plumbing may define __init__ —
    it's the sole owner of self._conn/_worker/_caches/_last_backup/_doc_name.
    A future mixin that adds its own __init__ without calling
    super().__init__() would silently shadow this one depending on
    base-class order; don't do that. Likewise, base-class order here is
    safe to change only because no two mixins define the same attribute
    name today — that invariant isn't automatically enforced, so keep new
    methods' names unique across the whole family when adding to any one
    mixin file."""
