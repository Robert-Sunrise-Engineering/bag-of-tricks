"""Directory scanning -- enumerate candidate documents.

Walks a directory tree and yields files whose (lowercased) suffix is in the
configured extension list, skipping dot-prefixed directories. No file
content is read here.
"""

import os
from pathlib import Path
from typing import Iterator


def iter_documents(root, extensions) -> Iterator[Path]:
    """
    Yield document paths under ``root`` whose suffix is in ``extensions``.

    Uses ``os.walk``, skips ``.``-prefixed directories (e.g. ``.git``,
    ``.pytest_cache``), and yields files sorted by absolute path. A
    nonexistent ``root`` yields nothing (the cli checks for a missing
    directory explicitly and exits 2 before calling this).

    Args:
        root: The directory to walk.
        extensions: A list of lowercased suffixes like ``[".pdf", ".tif"]``.

    Yields:
        Path objects for matching files, sorted by absolute path.
    """
    matches = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for filename in filenames:
            if os.path.splitext(filename)[1].lower() in extensions:
                matches.append(Path(os.path.join(dirpath, filename)))
    for path in sorted(matches, key=lambda p: os.path.abspath(p)):
        yield path
