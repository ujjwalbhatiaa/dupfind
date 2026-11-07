"""dupfind — hash-based duplicate file finder.

Groups files by size first, then SHA-256-hashes only the files that share
a size with at least one other file, so large trees are scanned cheaply.
"""

from .core import DuplicateGroup, ScanResult, find_duplicates, hash_file
from .cli import main

__all__ = ["DuplicateGroup", "ScanResult", "find_duplicates", "hash_file", "main"]
__version__ = "1.0.0"
