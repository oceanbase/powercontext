"""Load each independent plugin's modules without installing a namespaced package."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "powercontext"), str(ROOT / "powercontext_agent")]
