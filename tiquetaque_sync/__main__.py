"""Allow ``python -m tiquetaque_sync`` to behave exactly like the console script."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
