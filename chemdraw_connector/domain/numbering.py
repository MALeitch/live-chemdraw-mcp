"""Compound-numbering label generation."""
import string


def make_labels(count, start=1, scheme="numeric", group_sizes=None):
    """numeric: 1, 2, 3...  numeric-letter: with group_sizes=[1,3,2] ->
    1, 2a, 2b, 2c, 3a, 3b (groups of size 1 get a bare number)."""
    if scheme == "numeric":
        return [str(start + i) for i in range(count)]
    if scheme == "numeric-letter":
        sizes = group_sizes or [1] * count
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
