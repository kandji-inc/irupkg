import sys
from .irupkg import main


def _kpkg_alias() -> None:
    print("WARNING: 'kpkg' is deprecated, use 'irupkg' instead", file=sys.stderr)
    main()
