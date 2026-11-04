from dotenv import load_dotenv

import os

load_dotenv()


def get_env_value(key: str) -> str | None:
    return os.environ.get(key.upper(), None)
