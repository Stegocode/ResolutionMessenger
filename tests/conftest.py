"""Shared pytest fixtures for ResolutionMessenger.

The one non-obvious thing here: we add the repo root to sys.path
so `import ResolutionMessenger` works when pytest runs from the package
folder itself (e.g., `pytest ResolutionMessenger/tests`).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Repo root is two levels up from this file.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
