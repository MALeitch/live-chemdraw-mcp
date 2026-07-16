"""Compound-numbering label generation."""
import string


def make_labels(count, start=1, scheme="numeric", group_sizes=None):
    """numeric: 1, 2, 3...  numeric-letter: with group_sizes=[1,3,2] ->
    1, 2a, 2b, 2c, 3a, 3b (groups of size 1 get a bare number).

    numeric-letter has no way to infer grouping on its own — group_sizes
    is required for it, not optional (there is no automatic notion of
    "these structures belong together" to fall back on; the caller who
    knows why 3 structures share the number 2 must say so explicitly)."""
    if scheme == "numeric":
        return [str(start + i) for i in range(count)]
    if scheme == "numeric-letter":
        if group_sizes is None:
            raise ValueError(
                "scheme='numeric-letter' requires group_sizes (e.g. "
                "[1, 3, 2] for a bare '1' followed by '2a,2b,2c' then "
                "'3a,3b') — there is no way to infer grouping "
                "automatically. Use scheme='numeric' for plain "
                "sequential numbers instead."
            )
        sizes = group_sizes
        if sum(sizes) != count:
            raise ValueError(
                f"group_sizes {sizes} sums to {sum(sizes)}, expected {count}"
            )
        labels = []
        num = start
        for size in sizes:
            if size == 1:
                labels.append(str(num))
            else:
                if size > 26:
                    raise ValueError("numeric-letter groups support at most 26 members")
                for i in range(size):
                    labels.append(f"{num}{string.ascii_lowercase[i]}")
            num += 1
        return labels
    raise ValueError(f"Unknown numbering scheme {scheme!r}")
