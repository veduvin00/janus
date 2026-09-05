"""src — load .env once on first import so OPENAI_API_KEY (etc.) is picked
up automatically, without every entry point (run.py, api/main.py, scripts/,
pytest) needing its own dotenv call. Safe with no .env file present."""
import os
from dotenv import load_dotenv

load_dotenv()

# Normalize common typo (OPEN_API_KEY -> OPENAI_API_KEY)
if "OPENAI_API_KEY" not in os.environ and "OPEN_API_KEY" in os.environ:
    os.environ["OPENAI_API_KEY"] = os.environ["OPEN_API_KEY"]

