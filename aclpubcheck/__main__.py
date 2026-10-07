# __main__.py allows this library to be used with `python -m aclpubcheck`
import sys

from .formatchecker import main

if __name__ == "__main__":
    sys.exit(main())
