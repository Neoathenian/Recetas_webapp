import sys
import os
from pathlib import Path

# Set up the script directory and ensure it's in sys.path
script_directory = Path(__file__).resolve().parent
if str(script_directory) not in sys.path:
    sys.path.append(str(script_directory))

from dotenv import load_dotenv

from src.secrets import setup_secrets

import argparse
import uvicorn

# Import AFTER env is loaded

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8086)
    ap.add_argument("--env", type=str, default="dev")
    ap.add_argument("--debug-pages", action="store_true", help="Show every page regardless of privileges.")

    args = ap.parse_args()

    # If secrets are delivered via environment variables (Cloud Run), materialize them.
    if os.getenv("ENV_FILE") or os.getenv("SERVICE_ACCOUNT_KEY"):
        setup_secrets(args.env)

    # Load .env relative to this script so it works regardless of CWD
    env_path=script_directory / "secrets" / f"env.{args.env}"
    if env_path.exists():
        load_dotenv(env_path, override=True)
    else:
        raise FileNotFoundError(f"Could not find an environment file for '{args.env}'. ")

    if args.debug_pages:
        os.environ["THELIST_DEBUG_PRIVILEGES"] = "1"

     

    # Ensure TLS trust store is available for Cloud SQL connector / aiohttp
    if not os.getenv("SSL_CERT_FILE") or not os.path.exists(os.getenv("SSL_CERT_FILE", "")):
        try:
            import certifi  # type: ignore

            cert_path = certifi.where()
            os.environ.setdefault("SSL_CERT_FILE", cert_path)
            os.environ.setdefault("REQUESTS_CA_BUNDLE", cert_path)
        except Exception:
            pass

    #This is the last thing to do because first we need the secrets imported
    from app import app
    uvicorn.run(app, host="0.0.0.0", port=args.port)
