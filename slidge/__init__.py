"""
Most of slidge public API can be imported from this top level module.
"""

import warnings

from . import slixfix  # noqa: F401
from .command import FormField, SearchResult  # noqa: F401
from .contact import LegacyContact, LegacyRoster  # noqa: F401
from .core import config as global_config  # noqa: F401
from .core.gateway import BaseGateway  # noqa: F401
from .core.session import BaseSession  # noqa: F401
from .group import LegacyBookmarks, LegacyMUC, LegacyParticipant  # noqa: F401
from .util.db import GatewayUser, user_store  # noqa: F401
from .util.types import MucType  # noqa: F401
from .util.util import addLoggingLevel


def formatwarning(message, category, filename, lineno, line=""):
    return f"{filename}:{lineno}:{category.__name__}:{message}\n"


warnings.formatwarning = formatwarning


__all__ = [
    "BaseGateway",
    "BaseSession",
    # For backwards compatibility, these names are still importable from the
    # to top-level slidge module, but this is deprecated.
    # "GatewayUser",
    # "LegacyBookmarks",
    # "LegacyMUC",
    # "LegacyContact",
    # "LegacyParticipant",
    # "LegacyRoster",
    # "MucType",
    # "FormField",
    # "SearchResult",
    "user_store",
    "global_config",
]

addLoggingLevel()
