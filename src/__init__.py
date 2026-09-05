"""src — load .env once on first import so OPENAI_API_KEY (etc.) is picked
up automatically, without every entry point (run.py, api/main.py, scripts/,
pytest) needing its own dotenv call. Safe with no .env file present."""
from dotenv import load_dotenv

load_dotenv()
