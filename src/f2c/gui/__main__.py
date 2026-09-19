"""`python -m f2c.gui` - the same window the `f2c gui` command opens."""
import sys

from .app import main

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
