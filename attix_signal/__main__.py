"""Enable `python -m attix_signal` invocation."""
import sys

from .cli import main

sys.exit(main())
