"""Windowed bundle entry: Finder launches without terminal streams."""
import os
import sys
from fastturbo.main import main

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")
raise SystemExit(main())
