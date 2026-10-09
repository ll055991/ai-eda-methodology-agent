"""Allow ``python -m aemp`` as an alternative to the installed ``aemp`` entry point."""

import sys

from aemp.cli import main

if __name__ == "__main__":
    sys.exit(main())
