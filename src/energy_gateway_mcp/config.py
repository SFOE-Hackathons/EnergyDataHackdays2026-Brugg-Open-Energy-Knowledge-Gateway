import configparser
from pathlib import Path

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.ini"

_parser = configparser.ConfigParser()
if not _parser.read(_CONFIG_PATH):
    raise FileNotFoundError(f"Could not find config file at {_CONFIG_PATH}")

TOKEN_URL = _parser.get("gateway", "token_url")
GATEWAY_URL = _parser.get("gateway", "gateway_url")
PUBDB_BASE_URL = _parser.get("pubdb", "base_url")
