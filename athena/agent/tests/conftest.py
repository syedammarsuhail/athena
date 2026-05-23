"""Test config: ensure the repo root is on the import path so 'agent.graph...' resolves."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # /repo
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import os
os.environ.setdefault("ANTHROPIC_API_KEY", "fake-for-tests")
