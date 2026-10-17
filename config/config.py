"""
Minimal Configuration Loader.
Only loads database connection parameters from config.toml.
All pipeline/business configuration comes from the DB via MetadataLoader.
"""
import os
import toml
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOML_PATH = os.path.join(BASE_DIR, "config.toml")

if not os.path.exists(TOML_PATH):
    raise FileNotFoundError(f"Configuration file not found: {TOML_PATH}")

with open(TOML_PATH, "r") as f:
    toml_config = toml.load(f)

# ─────────────────────────────────────────────────────────────────────────────
# Database Connection Config (only thing from TOML)
# ─────────────────────────────────────────────────────────────────────────────
db_config = toml_config.get("database", {})

# ─────────────────────────────────────────────────────────────────────────────
# Logging Directory
# ─────────────────────────────────────────────────────────────────────────────
LOG_DIR = os.getenv("LOG_DIR") or toml_config.get("log_dir", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
