from dotenv import load_dotenv

load_dotenv()

import runpy

runpy.run_path("test_gateway.py", run_name="__main__")
