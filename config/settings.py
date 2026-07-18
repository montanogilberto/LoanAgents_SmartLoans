"""Environment configuration — loaded once, imported everywhere."""
import os

from dotenv import load_dotenv

load_dotenv()

# google-adk reads GEMINI_API_KEY (or GOOGLE_API_KEY) / GCP_PROJECT+GCP_LOCATION
# directly from the process environment — load_dotenv() above is what makes
# these visible to it. Exposed here too so other code can check they're set.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
GCP_PROJECT = os.environ.get("GCP_PROJECT", "")
GCP_LOCATION = os.environ.get("GCP_LOCATION", "")

SMARTLOANS_BACKEND_URL = os.environ.get("SMARTLOANS_BACKEND_URL", "https://smartloansbackend.azurewebsites.net").rstrip("/")
PORT = int(os.environ.get("PORT", "8080"))
