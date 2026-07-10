"""Logging configuration for the krogetter application."""

import logging


def setup_logging(level: str = "INFO") -> None:
    """Configure stdlib logging with a clean format.

    Args:
        level: One of DEBUG, INFO, WARNING, ERROR (case-insensitive).
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
