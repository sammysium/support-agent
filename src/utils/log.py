import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
)

logger = logging.getLogger(__name__)

def log_me(message: str):
    logger.info(message)


