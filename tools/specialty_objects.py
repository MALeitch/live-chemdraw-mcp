"""Native ChemDraw "specialty" objects: polymer brackets today, TLC plates/
other Phase B+ objects land here too later (see
chemdraw_connector/bridge/_specialty_objects.py).

NOTE: tool descriptions with dynamic content are passed via
@mcp.tool(description=...) -- a plain docstring is fine when there's no
shared fragment to interpolate, but an f-string is NEVER a docstring;
FastMCP would silently register the tool with no description (confirmed
live landmine, see tools/stereo.py's module docstring)."""
from ._common import as_json

_BRACKET_LIMITATION_DOC = (
    "Known limitations, confirmed live: (1) RepeatCount/SRULabel/"
    "ComponentOrder cannot be set via COM (both get and put reliably raise "
    "a COM exception) -- the only label text available is ChemDraw's own "
    "automatic abbreviation tied to bracket_usage (sru -> \"n\", monomer -> "
    "\"mon\", crosslink -> \"xl\", unspecified -> no label at all); (2) this "
    "is a purely decorative glyph pair, not bound to real chemistry -- the "
    "InsideAtoms/OutsideAtoms/ContainedAtoms/CrossingBonds ChemDraw exposes "
    "internally do NOT reliably reflect true geometric containment "
    "(confirmed inconsistent in testing) and are not surfaced here; (3) "
    "like chemdraw_make_arrow/chemdraw_make_symbol, this won't move if the "
    "wrapped structure is moved later, and brackets never appear in "
    "target=\"document\"/\"selection\" structure counts (doc.Brackets is a "
    "separate collection from doc.Groups); (4) NOT reliably fixed this "
    "session -- the opening/closing pair's mirrored hook orientation "
    "(\"[...]\" vs two identically-oriented marks) is confirmed live to be "
    "inconsistent once a second Bracket exists in the same automation "
    "session; always screenshot the result and be ready to flip the "
    "closing bracket by hand in the ChemDraw UI if it didn't mirror."
)


def register(mcp, bridge):
    @mcp.tool(description=(
        "Wrap a structure (or an explicit rectangle) with a matching PAIR "
        "of polymer/repeat-unit bracket marks -- an opening \"[\" and "
        "closing \"]\" (or curly/round equivalent), e.g. the standard "
        "\"[...]n\" SRU notation. target: structure to wrap (its bounding "
        "box + margin defines the rectangle) -- pass \"\" and give all of "
        "left/top/right/bottom explicitly instead for manual placement. "
        "bracket_type: square | curly | round -- confirmed live to render "
        "as visually distinct glyphs (straight hooked line / curly brace "
        "with a center bulge / big parenthesis-like arc). bracket_usage: "
        "sru | monomer | mer | copolymer | copolymer_alternating | "
        "copolymer_random | copolymer_block | crosslink | graft | "
        "modification | component | mixture_unordered | mixture_ordered | "
        "multiple_group | generic | anypolymer | unspecified -- drives "
        "ChemDraw's own automatic label on the CLOSING bracket only "
        "(confirmed live: sru -> \"n\", monomer -> \"mon\", crosslink -> "
        "\"xl\"; the opening bracket never carries a label, matching real "
        "ChemDraw convention). " + _BRACKET_LIMITATION_DOC))
    def chemdraw_make_bracket(target: str, bracket_type: str = "square",
                              bracket_usage: str = "sru",
                              left: float | None = None, top: float | None = None,
                              right: float | None = None, bottom: float | None = None,
                              margin: float = 10.0,
                              polymer_repeat_pattern: str = "") -> str:
        return as_json(bridge.make_bracket(
            target, bracket_type, bracket_usage, left, top, right, bottom,
            margin, polymer_repeat_pattern or None))

    @mcp.tool(description=(
        "Edit one existing bracket glyph's type/usage/repeat-pattern "
        "(object_id from chemdraw_make_bracket's opening_object_id/"
        "closing_object_id, or chemdraw_list_brackets). A pair made by "
        "chemdraw_make_bracket is two independent objects -- restyling "
        "both ends of the pair means calling this twice. Only params "
        "explicitly passed are changed."))
    def chemdraw_set_bracket_style(object_id: str, bracket_type: str = "",
                                   bracket_usage: str = "",
                                   polymer_repeat_pattern: str = "") -> str:
        return as_json(bridge.set_bracket_style(
            object_id, bracket_type or None, bracket_usage or None,
            polymer_repeat_pattern or None))

    @mcp.tool()
    def chemdraw_list_brackets() -> str:
        """Enumerate every bracket glyph in the document with its
        object_id, endpoints, type, and usage -- brackets aren't scoped to
        a structure, so this always covers the whole document. Each entry
        is ONE glyph (an opening or closing mark), not a matched pair."""
        return as_json(bridge.list_brackets())

    @mcp.tool(description=(
        "Draw a native TLC (thin-layer chromatography) plate for a "
        "reaction-monitoring figure: a rectangular plate outline with "
        "labeled Rf-spot lanes. left/top/right/bottom: the plate's "
        "rectangle in document points (72/inch, top-left origin) -- all "
        "four required (a TLC plate is a standalone illustration, not "
        "something wrapped around existing chemistry, so there is no "
        "target-to-wrap mode like chemdraw_make_bracket has). lanes: a "
        "JSON list of lanes (left-to-right); each lane is a list of spot "
        "objects {\"rf\": 0.0-1.0 (required), \"width\":, \"height\":, "
        "\"tail\": bool, \"filled\": bool, \"bold\": bool, \"dashed\": "
        "bool, \"show_rf\": bool}. CONFIRMED LIVE: a spot's vertical "
        "position is driven entirely by rf (ChemDraw linearly interpolates "
        "it between the plate's origin and solvent-front lines) -- no "
        "manual positioning is needed or possible, and lanes render as "
        "evenly spaced columns automatically. HARD LIMIT, confirmed live: "
        "each lane can hold AT MOST 2 spots (a 3rd is rejected here with a "
        "clear error -- ChemDraw itself would otherwise silently drop it "
        "with no error at all). \"show_rf\" prints ChemDraw's own "
        "auto-generated \"Rf = 0.NN\" label next to that spot. "
        "\"filled\"=false + \"dashed\"=true is confirmed to render as a "
        "visually distinct hollow dashed-outline circle. \"tail\" "
        "(comet-tail smear) is settable but its visual effect could not "
        "be confirmed at available screenshot resolution -- treat as "
        "experimental and screenshot to check before relying on it. "
        "origin_fraction/solvent_front_fraction (both default 0.1): 0-1 "
        "fractional position of the baseline/solvent-front lines along "
        "the plate. show_origin/show_solvent_front/show_borders/"
        "show_side_ticks/transparent: optional plate-level display "
        "toggles. KNOWN LIMITATION, same class as chemdraw_make_arrow/"
        "make_symbol/make_bracket: this is a free-floating annotation, "
        "not bound to any chemistry -- doc.TLCPlates never appears in "
        "target=\"document\"/\"selection\" structure counts, and won't "
        "move if nearby structures are moved later."))
    def chemdraw_make_tlc_plate(left: float, top: float, right: float,
                                bottom: float, lanes: list[list[dict]],
                                origin_fraction: float | None = None,
                                solvent_front_fraction: float | None = None,
                                show_origin: bool | None = None,
                                show_solvent_front: bool | None = None,
                                show_borders: bool | None = None,
                                show_side_ticks: bool | None = None,
                                transparent: bool | None = None) -> str:
        return as_json(bridge.make_tlc_plate(
            left, top, right, bottom, lanes, origin_fraction,
            solvent_front_fraction, show_origin, show_solvent_front,
            show_borders, show_side_ticks, transparent))

    @mcp.tool(description=(
        "Edit an existing TLC plate's display properties (object_id from "
        "chemdraw_make_tlc_plate or chemdraw_list_tlc_plates). Only params "
        "explicitly passed (not None) are changed. Does not touch lanes/"
        "spots -- rebuild via chemdraw_make_tlc_plate for structural "
        "changes (lane/spot count, Rf values)."))
    def chemdraw_set_tlc_plate_style(object_id: str,
                                     origin_fraction: float | None = None,
                                     solvent_front_fraction: float | None = None,
                                     show_origin: bool | None = None,
                                     show_solvent_front: bool | None = None,
                                     show_borders: bool | None = None,
                                     show_side_ticks: bool | None = None,
                                     transparent: bool | None = None) -> str:
        return as_json(bridge.set_tlc_plate_style(
            object_id, origin_fraction, solvent_front_fraction, show_origin,
            show_solvent_front, show_borders, show_side_ticks, transparent))

    @mcp.tool()
    def chemdraw_list_tlc_plates() -> str:
        """Enumerate every TLC plate in the document with its object_id,
        rectangle, origin/solvent-front fractions, and every lane's spot
        Rf values -- plates aren't scoped to a structure, so this always
        covers the whole document."""
        return as_json(bridge.list_tlc_plates())
