"""Make the mcp-gateway package root importable regardless of invocation cwd."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
