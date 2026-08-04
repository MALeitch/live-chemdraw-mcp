"""ChemDraw binary .cdx parser — pure Python, zero COM, zero deps.

Reads a raw .cdx byte stream into the SAME plain-dict shape
`cdxml_graph.parse_element` already produces, so `cdxml_document.parse_document`
and everything downstream works on `.cdx` with no ChemDraw.

Format (confirmed live against real files):
  header: b'VjCD0100' + 4 version bytes + 16 reserved (28 bytes total)
  then a flat tag stream:
    tag uint16
      tag == 0x0000            -> end of current object
      tag & 0x8000             -> object start; uint32 id follows; recurse
      else                     -> property; uint16 len (0xFFFF => uint32 len)

Objects (confirmed live):
  Fragment 0x8003, Node 0x8004, Bond 0x8005, Text 0x8006

Properties (confirmed live):
  2DPosition 0x0200      (two INT32, stored y-then-x, units 1/65536 pt)
  Node_Type 0x0400
  Node_Element 0x0402
  Atom_Charge 0x0421
  Bond_Order 0x0600      (bitmask: 0x01=single, 0x02=double, 0x04=triple,
                          0x80=aromatic, 0x400=dative)
  Bond_Begin 0x0604
  Bond_End   0x0605

Three nesting bugs the prototype found (all silent, fragment counts pass):
1. A nickname node (Ph, TBS) carries its expansion as a NESTED Fragment
   inside the Node. Hoisting those atoms into the parent inflates every
   molecule using a shorthand (a Ph adds 6 phantom carbons). Track fragment
   depth, collect only depth-1 -- but KEEP the nickname node itself as one
   pseudo-atom, which is what CDXML does.
2. With a single "current node" variable, the INNER nodes of that expansion
   clobber the outer node's in-progress record, so every nickname atom
   vanishes instead. Guard all node/bond mutation on `node_depth == 1`.
3. Flush-vs-decrement ordering: flushing a node while still nominally
   "inside" it drops 100% of atoms (took recall from 21% -> 0%).

Verification method (reusable): run `chemdraw_convert_cdx_cdxml` on real .cdx
files to get ChemDraw's own ground-truth CDXML, parse both sides, compare
canonical-SMILES multisets. Result across 8 real files (3-370 structures,
incl. 6.5 MB thesis file): 518/518 fragments, 514 SMILES matched = 99.2%.
"""
import struct
from typing import List, Dict, Any, Optional, Tuple


# Magic and header
MAGIC = b'VjCD0100'
HEADER_SIZE = 28

# Object tags (high bit set = object start)
O_PAGE        = 0x8001
O_GROUP       = 0x8002
O_FRAGMENT    = 0x8003
O_NODE        = 0x8004
O_BOND        = 0x8005
O_TEXT        = 0x8006

# Property tags
P_2DPOS       = 0x0200
P_NODE_TYPE   = 0x0400
P_ELEMENT     = 0x0402
P_ISOTOPE     = 0x0420
P_CHARGE      = 0x0421
P_BOND_ORDER  = 0x0600
P_BOND_DISPLAY = 0x0601
P_BOND_BEGIN  = 0x0604
P_BOND_END    = 0x0605
P_TEXT        = 0x0700

# CDX bond order bitmask -> the SAME ints cdxml_graph._ORDER_MAP produces.
#
# This must stay an int, and must stay inside cdxml_graph's vocabulary
# {1, 2, 3, 4, 15}. An earlier version emitted strings ('1', '2', '1.5')
# while cdxml_graph emitted ints -- and cdx_document._rdkit_bond_type keys on
# ints, so every string fell through .get()'s default and became a SINGLE
# bond. Nothing raised. Benzene parsed as C6H12 and acetone as C3H8O; the
# formulas were simply wrong, on every molecule containing any non-single
# bond.
#
# Orders CDX can express but CDXML's Order attribute cannot (quintuple,
# sextuple, dative) fall through to 1 -- exactly what cdxml_graph does with
# an unrecognised Order string, so both front ends agree.
BOND_ORDER_MAP = {
    0x0001: 1,     # single
    0x0002: 2,     # double
    0x0004: 3,     # triple
    0x0008: 4,     # quadruple
    0x0040: 1,     # half        -- cdxml_graph maps "0.5" -> 1
    0x0080: 15,    # one-and-a-half (Kekule aromatic) -- "1.5" -> 15
    0x0100: 2,     # two-and-a-half -- "2.5" -> 2
}

# kCDXProp_Bond_Display -> the string CDXML writes in its Display attribute.
#
# "Begin"/"End" name which ATOM the narrow point of the wedge sits on, so the
# pair is directional: swapping WedgeBegin for WedgeEnd inverts the
# stereocentre. Never normalise bond endpoints without carrying this along.
BOND_DISPLAY_MAP = {
    0: 'Solid',
    1: 'Dash',
    2: 'Hash',
    3: 'WedgedHashBegin',
    4: 'WedgedHashEnd',
    5: 'Bold',
    6: 'WedgeBegin',
    7: 'WedgeEnd',
    8: 'Wavy',
    9: 'HollowWedgeBegin',
    10: 'HollowWedgeEnd',
    11: 'WavyWedgeBegin',
    12: 'WavyWedgeEnd',
    13: 'Dot',
    14: 'DashDot',
}

# CDX node type -> string (matching CDXML NodeType values)
NODE_TYPE_MAP = {
    0: 'Unspecified', 1: 'Element', 2: 'ElementList',
    3: 'ElementListNickname', 4: 'Nickname', 5: 'Fragment',
    6: 'Formula', 7: 'GenericNickname', 8: 'AnonymousAlternativeGroup',
    9: 'NamedAlternativeGroup', 10: 'MultiAttachment',
    11: 'VariableAttachment', 12: 'ExternalConnectionPoint',
    13: 'LinkNode', 14: 'Monomer',
}

# Node types that behave as a plain atom of a real element
_REAL_ATOM_NODETYPES = {None, "", "Element", "Unspecified"}


def parse(buf: bytes) -> List[Dict[str, Any]]:
    """Parse a .cdx byte buffer into the same dict shape as cdxml_graph.parse_element.

    Returns a LIST of fragments, one per top-level molecule on the page:
      [{"nodes": [...], "bonds": [...]}, ...]
      nodes: {"id", "element", "charge", "node_type", "is_real_atom",
              "num_hydrogens", "label"}
      bonds: {"begin", "end", "order"}  (order "1.5" == aromatic)

    Fragment boundaries are preserved deliberately -- collapsing them would
    merge separate molecules into one graph and silently corrupt every
    downstream SMILES.

    A buffer with no CDX payload returns [] -- an EMPTY LIST, not a bare
    {"nodes": [], "bonds": []}. That dict was the original return here and it
    is the same type the loop below yields per fragment, so `for f in
    parse(buf)` on a malformed blob iterated the dict's KEYS ("nodes",
    "bonds") as if they were fragments instead of doing nothing. Office
    embeddings include Ctrl+V pastes carrying no chemistry at all, so the
    no-payload path is routine, not exotic.
    """
    i = buf.find(MAGIC)
    if i < 0:
        return []
    pos = i + HEADER_SIZE
    n = len(buf)

    # Per-fragment state
    fragments: List[Dict[str, Any]] = []
    cur_frag_nodes: List[Dict[str, Any]] = []
    cur_frag_bonds: List[Dict[str, Any]] = []

    # Parser state stack
    stack: List[int] = []  # object tags currently open
    pending: Dict[str, Any] = {}

    # Current object being built
    cur_node: Optional[Dict[str, Any]] = None
    cur_bond: Optional[Dict[str, Any]] = None

    # Nesting depth trackers
    frag_depth = 0      # depth of Fragment objects; only depth 1 is a real molecule
    node_depth = 0      # depth of Node objects; nickname inner fragment lives inside a Node
    text_depth = 0      # depth of Text objects nested inside the current Node
    # (a labeled atom's visible symbol is rendered via a child Text object,
    # e.g. Node[2DPosition=atom center]{ Text[2DPosition=label anchor]{...} }.
    # Both objects carry a 2DPosition, and the Text's is the label's own
    # rendering anchor -- offset from the atom's real coordinate by whatever
    # ChemDraw's text-metrics offset is, not a chemistry-meaningful value.
    # Without this counter, node_depth alone can't tell "this 2DPosition is
    # the Node's own" from "this 2DPosition belongs to the Text nested
    # inside it", so the label's position silently overwrote the atom's own
    # already-correctly-read position right before flush_node() used it --
    # every LABELED atom (any heteroatom, explicit H, nickname) got the
    # wrong coordinate while plain unlabeled carbons were fine. See
    # tests/test_cdx_binary.py::test_labeled_atom_keeps_its_own_position.)

    def collecting() -> bool:
        """Only take nodes/bonds belonging to the OUTERMOST fragment.

        A nickname node (e.g. 'Ph') carries its own expansion as a nested
        fragment; ChemDraw's CDXML keeps that inside the <n>, so we must not
        hoist its atoms into the parent or the molecule gains phantom atoms.
        The nickname node ITSELF is kept -- it is a real atom of the parent.
        """
        return cur_frag_nodes is not None and frag_depth == 1

    def flush_node():
        nonlocal cur_node, pending
        if cur_node is not None and collecting():
            # Default element is carbon (6) if not specified
            element = pending.get('element', 6)
            node_type_str = NODE_TYPE_MAP.get(pending.get('ntype', 1), 'Element')
            is_real = node_type_str in _REAL_ATOM_NODETYPES
            
            # Get label - nicknames carry their text in P_TEXT
            label = pending.get('label', '')
            
            node_entry = {
                "id": cur_node['id'],
                "element": element,
                "charge": pending.get('charge', 0),
                "node_type": node_type_str,
                "is_real_atom": is_real,
                "num_hydrogens": None,  # CDX doesn't seem to carry explicit H count
                "label": label,  # Store label for nickname encoding
                # (x, y) in points, or None when the node carries no
                # 2DPosition. Callers that re-derive coordinates (RDKit
                # Compute2DCoords, per-unit Clean) ignore this, but anything
                # asking a geometric question -- bounds, overlap, off-page --
                # needs it, and canvas.py silently reports "no violations"
                # rather than "unknown" when it is missing.
                "pos": pending.get('pos'),
            }
            cur_frag_nodes.append(node_entry)
        cur_node = None
        pending.clear()

    def flush_bond():
        nonlocal cur_bond, pending
        if cur_bond is not None and collecting():
            b = pending.get('begin')
            e = pending.get('end')
            if b is not None and e is not None:
                cur_frag_bonds.append({
                    "begin": b,
                    "end": e,
                    "order": pending.get('order', 1),
                    # None == the bond carried no Display property at all
                    # (an ordinary line). That is deliberately distinct from
                    # 'Solid', which is an explicit "flat" statement.
                    "display": pending.get('display'),
                })
        cur_bond = None
        pending.clear()

    while pos + 2 <= n:
        tag = struct.unpack_from('<H', buf, pos)[0]
        pos += 2

        if tag == 0x0000:                       # end of object
            if not stack:
                break
            closed = stack.pop()
            if closed == O_NODE:
                if node_depth == 1:      # a node of the molecule itself
                    flush_node()
                node_depth -= 1
            elif closed == O_BOND:
                if node_depth == 0:
                    flush_bond()
            elif closed == O_FRAGMENT:
                frag_depth -= 1
                if frag_depth == 0:
                    # Fragment complete - save it if it has nodes
                    if cur_frag_nodes:
                        fragments.append({
                            "nodes": cur_frag_nodes,
                            "bonds": cur_frag_bonds,
                        })
                    cur_frag_nodes = []
                    cur_frag_bonds = []
            elif closed == O_TEXT:
                if text_depth > 0:
                    text_depth -= 1
            continue

        if tag & 0x8000:                        # object start
            if pos + 4 > n:
                break
            oid = struct.unpack_from('<I', buf, pos)[0]
            pos += 4
            stack.append(tag)
            if tag == O_FRAGMENT:
                frag_depth += 1
                if frag_depth == 1:
                    # Start a new top-level fragment
                    cur_frag_nodes = []
                    cur_frag_bonds = []
            elif tag == O_NODE:
                node_depth += 1
                if node_depth == 1:      # inner nodes must not clobber this
                    flush_node()  # flush any previous node at depth 1
                    cur_node = {'id': oid}
            elif tag == O_BOND:
                if node_depth == 0:      # bonds inside a nickname aren't ours
                    flush_bond()  # flush any previous bond
                    cur_bond = {'id': oid}
            elif tag == O_TEXT:
                text_depth += 1
            continue

        # property
        if pos + 2 > n:
            break
        ln = struct.unpack_from('<H', buf, pos)[0]
        pos += 2
        if ln == 0xFFFF:
            if pos + 4 > n:
                break
            ln = struct.unpack_from('<I', buf, pos)[0]
            pos += 4
        if pos + ln > n:
            break
        data = buf[pos:pos + ln]
        pos += ln

        if cur_node is not None and node_depth == 1:
            # P_TEXT is the label Text child's own content and must always
            # be read regardless of text_depth (it only ever appears inside
            # that nested object). Everything else here is the Node's OWN
            # property and is gated on text_depth == 0 so a nested label
            # Text object's properties -- its own 2DPosition especially, see
            # text_depth's declaration above -- never reach here.
            if tag == P_TEXT:
                # Styled text - simplified extraction
                if len(data) >= 2:
                    nruns = struct.unpack_from('<H', data, 0)[0]
                    off = 2 + nruns * 10
                    if off <= len(data):
                        try:
                            pending['label'] = data[off:].decode('utf-8', 'replace').strip('\x00').strip()
                        except Exception:
                            pending['label'] = ''
            elif text_depth == 0:
                if tag == P_ELEMENT and ln >= 2:
                    pending['element'] = struct.unpack_from('<h', data, 0)[0]
                elif tag == P_2DPOS and ln >= 8:
                    # Stored as y-then-x, units of 1/65536 pt
                    y, x = struct.unpack_from('<ii', data, 0)
                    pending['pos'] = (x / 65536.0, y / 65536.0)
                elif tag == P_NODE_TYPE and ln >= 2:
                    pending['ntype'] = struct.unpack_from('<h', data, 0)[0]
                elif tag == P_CHARGE:
                    # Charge can be int8 or int32 (divided by 65536 for fixed-point)
                    if ln == 1:
                        pending['charge'] = struct.unpack_from('<b', data, 0)[0]
                    elif ln >= 4:
                        pending['charge'] = struct.unpack_from('<i', data, 0)[0] // 65536
                elif tag == P_ISOTOPE and ln >= 2:
                    pending['isotope'] = struct.unpack_from('<h', data, 0)[0]
        elif cur_bond is not None and node_depth == 0:
            if tag == P_BOND_ORDER and ln >= 2:
                order_val = struct.unpack_from('<H', data, 0)[0]
                pending['order'] = BOND_ORDER_MAP.get(order_val, 1)
            elif tag == P_BOND_DISPLAY and ln >= 2:
                disp = struct.unpack_from('<h', data, 0)[0]
                # An unrecognised code is surfaced rather than dropped: a
                # silent None would assert "this bond is flat", which is the
                # one thing we cannot know here.
                pending['display'] = BOND_DISPLAY_MAP.get(
                    disp, f'Unknown({disp})')
            elif tag == P_BOND_BEGIN and ln >= 4:
                pending['begin'] = struct.unpack_from('<I', data, 0)[0]
            elif tag == P_BOND_END and ln >= 4:
                pending['end'] = struct.unpack_from('<I', data, 0)[0]

    # Handle any trailing fragment
    if cur_frag_nodes:
        fragments.append({
            "nodes": cur_frag_nodes,
            "bonds": cur_frag_bonds,
        })

    # Return list of fragments, each with nodes and bonds (matching cdxbin.py's Frag output)
    # This preserves fragment boundaries which is crucial for correct SMILES matching
    return fragments


def parse_file(path: str) -> List[Dict[str, Any]]:
    """Parse a .cdx file from disk. Returns list of fragments, each with nodes and bonds."""
    with open(path, 'rb') as fh:
        return parse(fh.read())