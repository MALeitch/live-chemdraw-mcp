"""Shared pure bounding-box containment primitive, used by two otherwise
unrelated layers that both need to detect a ChemDraw-created "wrapper"
group around other units:

- bridge/_plumbing.py's _split_wrapper_groups (live COM, right after
  inserting a multi-fragment payload like an ionic salt -- reads
  .Left/.Top/.Right/.Bottom off real doc.Groups objects).
- domain/canvas.py's find_union_wrapper_duplicates (pure, on
  already-extracted bounds dicts from a document snapshot, run on every
  later get_document_state/describe_canvas call).

Extracted so "strictly contains" can never quietly drift into two
different definitions between the insert-time check and the read-time
check -- both call this same function."""


def find_containing_wrappers(boxes, tol=0.5):
    """boxes: list of (left, top, right, bottom) tuples. Returns
    {i: [j, ...]} for every box i whose bounds strictly contain one or
    more other boxes j in the same list -- all 4 edges within tol, and
    strictly larger area (not just touching/equal). A box containing
    nothing is simply absent from the returned dict (never an empty
    list)."""
    def area(b):
        return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])

    def strictly_contains(outer, inner):
        return (outer[0] <= inner[0] + tol and outer[1] <= inner[1] + tol
                and outer[2] >= inner[2] - tol and outer[3] >= inner[3] - tol
                and area(outer) > area(inner) + tol)

    out = {}
    for i, outer in enumerate(boxes):
        kids = [j for j, inner in enumerate(boxes)
                if j != i and strictly_contains(outer, inner)]
        if kids:
            out[i] = kids
    return out
