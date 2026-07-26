"""
Logging Configuration

Developed by: LastPerson07 × AkBots
Telegram: @AkBots_Official | @THEUPDATEDGUYS
"""

import logging
from logging.handlers import RotatingFileHandler


# ==========================================
# Logging Setup
# ==========================================

# Log format for standard output
SHORT_LOG_FORMAT = "[%(asctime)s - %(levelname)s] - %(name)s - %(message)s"

# Detailed format (useful for debugging)
FULL_LOG_FORMAT = "%(asctime)s - [%(levelname)s] - %(name)s - %(message)s (%(filename)s:%(lineno)d)"


# Configure logging system
logging.basicConfig(
    level=logging.INFO,
    format=SHORT_LOG_FORMAT,
    handlers=[
        # Rotate logs: maximum 5MB per file, keep last 10 backups
        RotatingFileHandler("logs.txt", maxBytes=5 * 1024 * 1024, backupCount=10),

        # Output logs to console
        logging.StreamHandler()
    ]
)


# Reduce noisy logs from external libraries
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)


def LOGGER(name: str) -> logging.Logger:
    """
    Return a configured logger instance.

    Args:
        name (str): Name of the logger.

    Returns:
        logging.Logger: Logger object.
    """
    return logging.getLogger(name)
