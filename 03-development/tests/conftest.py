"""03-development pytest configuration.

Adds `<repo>/03-development/src` to sys.path so `from taskq_api.app
import app` resolves at collection time. Required because the package
uses a src-layout (SPEC.md §5) and no editable install is in play.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))