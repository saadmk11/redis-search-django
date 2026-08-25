"""Deterministic demo embedder. Not a real language model.

Theme words share a slot (audio, trail, search, kitchen, …) so
``knn("wireless headphones")`` ranks earbuds above cookware. Other tokens
get a small hashed remainder so products still differ. Swap this for a real
model in production — the Document hook stays the same.
"""

from __future__ import annotations

import hashlib
import math
import re

DIMS = 32
_TOKEN = re.compile(r"[a-z0-9]+")

# First N dimensions are dedicated themes. Synonyms share a slot.
_THEMES: tuple[frozenset[str], ...] = (
    frozenset(
        {
            "wireless",
            "audio",
            "headphone",
            "earbud",
            "speaker",
            "microphone",
            "sound",
            "music",
            "listen",
            "podcast",
            "voice",
            "noise",
        }
    ),
    frozenset(
        {
            "running",
            "trail",
            "shoe",
            "hiking",
            "outdoor",
            "workout",
            "road",
            "terrain",
        }
    ),
    frozenset(
        {
            "redis",
            "search",
            "index",
            "query",
            "django",
            "document",
            "ranking",
            "engine",
            "prefix",
        }
    ),
    frozenset(
        {
            "kitchen",
            "coffee",
            "cook",
            "skillet",
            "mug",
            "pour",
            "dripper",
            "recipe",
            "board",
        }
    ),
    frozenset(
        {
            "wool",
            "merino",
            "linen",
            "warm",
            "hoodie",
            "beanie",
            "blanket",
            "throw",
            "napkin",
        }
    ),
    frozenset({"desk", "keyboard", "laptop", "lamp", "hub", "notebook"}),
    frozenset({"camera", "video", "waterproof", "action"}),
    frozenset({"yoga", "mat", "recovery", "roller", "foam", "stretch"}),
)


def _stems(text: str) -> set[str]:
    stems: set[str] = set()
    for token in _TOKEN.findall(text.lower()):
        stems.add(token)
        if token.endswith("s") and len(token) > 4:
            stems.add(token[:-1])
        if token.endswith("ing") and len(token) > 6:
            stems.add(token[:-3])
    return stems


def embed(text: str) -> list[float]:
    """Turn text into a unit vector of ``DIMS`` floats."""
    vec = [0.0] * DIMS
    stems = _stems(text)
    for index, words in enumerate(_THEMES):
        vec[index] = float(len(stems & words))
    hashed_from = len(_THEMES)
    for token in stems:
        digest = hashlib.blake2b(token.encode(), digest_size=4).digest()
        slot = hashed_from + (int.from_bytes(digest, "little") % (DIMS - hashed_from))
        vec[slot] += 0.2
    norm = math.sqrt(sum(value * value for value in vec))
    if norm == 0:
        return vec
    return [value / norm for value in vec]
