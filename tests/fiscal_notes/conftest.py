"""fiscal_notes lives at the repo root (outside every workspace package, so the cache fingerprint
does not read it); put the root on the path the way the other tests do for ``scripts/``."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
