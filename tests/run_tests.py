"""Standard library test runner — no pytest required.

    python tests/run_tests.py
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tests import _bootstrap  # noqa: E402,F401  (isolates config/data before imports)


def build_suite() -> unittest.TestSuite:
    loader = unittest.TestLoader()
    return loader.discover(
        start_dir=str(REPO_ROOT / "tests"),
        pattern="test_*.py",
        top_level_dir=str(REPO_ROOT),
    )


def main() -> int:
    result = unittest.TextTestRunner(verbosity=2).run(build_suite())
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
