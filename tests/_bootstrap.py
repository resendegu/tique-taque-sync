"""Test bootstrap: isolate config/data in a temp dir before importing the app.

Importing ``tiquetaque_sync.main`` builds the runtime (and therefore the SQLite
database), so the sandbox has to be in place *before* any package import.
Working from inside the sandbox also keeps a developer's real ``.env`` out of
the assertions, since pydantic-settings resolves it relative to the CWD.
"""

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SANDBOX = Path(tempfile.mkdtemp(prefix="tiquetaque-sync-tests-"))
os.environ["TIQUETAQUE_SYNC_HOME"] = str(SANDBOX)

_ORIGINAL_CWD = Path.cwd()
os.chdir(SANDBOX)


def _cleanup() -> None:
    os.chdir(_ORIGINAL_CWD)
    shutil.rmtree(SANDBOX, ignore_errors=True)


atexit.register(_cleanup)
