"""Local dev entrypoint: loads ../.env, then runs server.py.

Docker runs server.py directly with env vars injected via `docker run
--env-file`, so server.py itself has no dotenv dependency.
"""

from dotenv import load_dotenv

load_dotenv()

import runpy
import sys

sys.path.insert(0, "mcp-gateway")

runpy.run_path("mcp-gateway/server.py", run_name="__main__")
