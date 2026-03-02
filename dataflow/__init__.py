import logging

_logger = None
def get_logger():
    global _logger
    if _logger is None:
        _logger = logging.getLogger("dataflow")
        if not _logger.handlers:
            handler = logging.StreamHandler()
            fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            handler.setFormatter(fmt)
            _logger.addHandler(handler)
        _logger.setLevel(logging.INFO)
    return _logger

