import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.propagate = False # disabling propagation to avoid double logging

if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(_handler)
