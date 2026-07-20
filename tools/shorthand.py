"""Shorthand/nickname condensation tools.

NOTE: docstrings here must be plain string literals (or passed via the
description= argument). An f-string is not a docstring — FastMCP would
silently register the tool with no description, which is exactly how
chemdraw_contract_to_shorthand's semantics got misread once, collapsing
whole molecules into "Ph" labels.
"""
from ._common import TARGET_DOC, as_json
from .structure import _parse


def register(mcp, bridge):
    # Catalog pulled through bridge.available_shorthand_groups() (not by
    # importing chemdraw_connector.domain.substructures.SHORTHANDS
    # directly here) — every tool call funnels through bridge, per
    # bridge/__init__.py's module docstring, and that includes this
    # docstring-building read of the group catalog, not just the mutating
    # calls below.
    catalog = bridge.available_shorthand_groups()
    auto = ", ".join(s["label"] for s in catalog if s["auto"])
    opt_in = ", ".join(s["label"] for s in catalog if not s["auto"])

    contract_group_desc = f"""Find known functional groups inside the target
structure(s) and contract JUST those atoms into their standard shorthand
labels, leaving the rest of the molecule drawn — e.g. collapse a drawn
monosubstituted benzene ring into "Ph", a drawn SiEt3 into "TES", a drawn
tert-butyl carbamate into "Boc". This is the tool for "abbreviate the
phenyl rings / protecting groups".

groups: "auto" contracts every group in the conservative default set; or a
comma-separated list of labels (case-insensitive) to contract only those.
Default set: {auto}.
Opt-in (matched only when named explicitly): {opt_in}.

Groups are matched with exactly one attachment bond, biggest first, never
overlapping, and never a whole molecule. Rings drawn with an aromatic
circle (rather than alternating double bonds) cannot be matched and are
skipped. Any structure with at least one contraction is automatically
cleaned up afterward (Clean Up Structure) — collapsing atoms into a label
can leave the REMAINING drawn portion with distorted bond angles, confirmed
live. Its caption is NOT auto-repositioned though, since contraction (like
any resize) can change where the caption should sit — call
chemdraw_fix_caption_gaps afterward. A .cdxml backup of the document is
saved first; the result includes backup_path.

Returns `results` (one entry per structure, each with `id`/`contracted`/
optional `note`) and `failed` — any structure whose contraction round(s)
raised partway through (e.g. a stale-handle failure), reported as
`{{"id", "error", "contracted_before_failure"}}` rather than aborting the
rest of the batch. {TARGET_DOC}"""

    @mcp.tool(description=contract_group_desc)
    def chemdraw_contract_group(target: str = "selection",
                                groups: str = "auto") -> str:
        return as_json(bridge.contract_functional_groups(
            _parse(target), groups))

    @mcp.tool(description=(
        "Collapse each ENTIRE target structure into one single shorthand "
        "label. The whole molecule becomes one nickname node displaying "
        "`label` (default: its molecular formula) — nothing stays drawn. "
        "Only use this when a complete fragment on the canvas should become "
        "a label. To abbreviate a functional group INSIDE a larger molecule "
        "(phenyl to Ph, carbamate to Boc...), use chemdraw_contract_group "
        "instead. The label text is display-only and NOT validated against "
        "the structure; the real atoms stay nested inside the label and "
        "chemdraw_expand_shorthand restores them. A .cdxml backup of the "
        "document is saved first; the result includes backup_path. Returns "
        "`contracted` (one entry per successfully contracted structure) and "
        "`failed` (per-structure `{\"id\", \"error\"}` for any structure "
        "that could not be contracted — the rest of the batch still runs). "
        + TARGET_DOC))
    def chemdraw_contract_to_shorthand(target: str = "selection",
                                       label: str = "") -> str:
        return as_json(bridge.contract_to_shorthand(_parse(target), label))

    @mcp.tool(description=(
        "Expand shorthand labels (Ph, TES, Boc...) in the target "
        "structure(s) back into fully drawn structures, one COM call per "
        "structure. Best for a SMALL/targeted target (one structure, a "
        "handful via a list, or 'selection') — e.g. the recovery path when "
        "one contraction produced the wrong label, since the original atoms "
        "live inside the label and are restored exactly. For expanding "
        "labels across a whole document, chemdraw_expand_labels is much "
        "faster (one call total instead of one per structure) and can "
        "filter by label text. A .cdxml backup of the document is saved "
        "first; the result includes backup_path. Returns `expanded` (one "
        "entry per successfully expanded structure) and `failed` (per-"
        "structure `{\"id\", \"error\"}` for any structure that could not "
        "be expanded — the rest of the batch still runs). " + TARGET_DOC))
    def chemdraw_expand_shorthand(target: str = "selection") -> str:
        return as_json(bridge.expand_shorthand(_parse(target)))

    @mcp.tool(description=(
        "Bulk-expand shorthand labels back into fully drawn structures in "
        "ONE ChemDraw call, across the whole target at once — the tool for "
        "\"expand every Ph in this document\" or \"expand everything\". "
        "Much faster than chemdraw_expand_shorthand on more than a few "
        "structures, because it does one document-wide pass instead of one "
        "call per structure, and this batching is chemically safe for "
        "expansion (unlike contraction — see chemdraw_contract_group's "
        "docs for why contraction can't be batched the same way).\n\n"
        "labels: \"all\" (default) expands every contracted label found; "
        "or a comma-separated list of label text to restrict which ones "
        "expand, case-insensitive — e.g. labels=\"Ph\" to expand only "
        "phenyl labels and leave TES/Boc/etc. untouched, or \"Ph,Bn\" for "
        "more than one. This is how to serve a request like \"only expand "
        "the phenyl rings.\"\n\n"
        "A .cdxml backup of the document is saved first; the result "
        "reports how many atoms were expanded, broken down per label text. "
        + TARGET_DOC))
    def chemdraw_expand_labels(target: str = "document",
                               labels: str = "all") -> str:
        return as_json(bridge.expand_labels(_parse(target), labels))

    @mcp.tool()
    def chemdraw_list_shorthand_groups() -> str:
        """The catalog of functional groups chemdraw_contract_group can
        recognize: label, SMARTS pattern, description, aliases, and whether
        each is in the "auto" default set."""
        return as_json({"groups": bridge.available_shorthand_groups()})
