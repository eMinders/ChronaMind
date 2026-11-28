"""Compatibility entrypoint for local API development."""

import os
import sys
from pathlib import Path
import runpy

API_DIR = Path(__file__).parent / "apps" / "api"
os.chdir(API_DIR)
sys.path.insert(0, str(API_DIR))
runpy.run_path(str(API_DIR / "run.py"), run_name="__main__")
