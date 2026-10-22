"""
Reads settings from .env (never committed): the database connection and the
SECRET_KEY that signs login session cookies. URL.create escapes any special
characters in the password, so it never has to be hand-encoded.
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"]


def database_url(database: str | None = None) -> str:
    """Connection URL from .env. Pass `database` to target another schema (e.g. the test database)."""
    load_dotenv(ROOT / ".env")
    missing = [key for key in REQUIRED if not os.getenv(key)]
    if missing:
        raise RuntimeError(
            f"Missing database settings: {', '.join(missing)}. "
            f"Copy .env.example to .env and fill it in."
        )
    url = URL.create(
        "mysql+pymysql",
        username=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        database=database or os.environ["DB_NAME"],
        query={"charset": "utf8mb4"},
    )
    return url.render_as_string(hide_password=False)


def secret_key() -> str | None:
    load_dotenv(ROOT / ".env")
    return os.getenv("SECRET_KEY")
