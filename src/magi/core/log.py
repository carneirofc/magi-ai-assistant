"""Typed logging for strict-mode modules.

agno's `log_info` / `log_warning` are unannotated, which strict type checking
reports as unknown. These wrappers log through agno's *current* logger (read
at call time, since agno swaps it between agent/team loggers), so output is
identical. New `core` code imports from here.
"""

import logging

from agno.utils import log as _agno_log


def log_info(msg: str) -> None:
    # The stdlib method, bound to agno's logger: AgnoLogger.info's extra
    # (untyped) parameters only add header-centering, which we never use.
    logging.Logger.info(_agno_log.logger, msg)


def log_warning(msg: str) -> None:
    _agno_log.logger.warning(msg)
