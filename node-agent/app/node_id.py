import re

# node_id is a free-form string per CONTRACT.md
# (`^[a-zA-Z0-9]+-[a-zA-Z0-9]+-[a-zA-Z0-9]+$`), not guaranteed to have a
# purely numeric final segment — so this can't just int()-parse a trailing
# number. Splitting into alternating digit/non-digit runs and comparing
# digit runs numerically (leaving everything else as plain string
# comparison) is what makes "edge10" sort after "edge2" instead of before
# it, which plain string comparison gets wrong past 9 nodes in a cluster
# (found while reviewing the Bully election's "highest node_id wins" rule
# at this project's current 3-node scale — not yet triggered, but a latent
# bug once a cluster grows into double digits).
_SEGMENT_RE = re.compile(r"\d+|\D+")


def sort_key(node_id: str) -> tuple:
    """Natural sort key for node_id — safe to use with >, <, sorted(), etc."""
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in _SEGMENT_RE.findall(node_id))
