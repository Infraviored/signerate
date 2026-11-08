import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Importing app runs init_sets(); never let that touch the real sets/ directory.
os.environ.setdefault("SIGNERATE_SETS_DIR", tempfile.mkdtemp(prefix="signerate-import-"))
