import warnings

from . import slixfix  # noqa: F401
from .core import config as global_config
from .core.command import FormField, SearchResult
from .core.contact import LegacyContact, LegacyRoster
from .core.gateway import BaseGateway
from .core.muc import LegacyBookmarks, LegacyMUC, LegacyParticipant, MucType
from .core.session import BaseSession
from .util.db import GatewayUser, user_store
from .util.util import addLoggingLevel


def formatwarning(message, category, filename, lineno, line=""):
    return f"{filename}:{lineno}:{category.__name__}:{message}\n"


warnings.formatwarning = formatwarning


__all__ = [
    "BaseGateway",
    "BaseSession",
    "GatewayUser",
    "LegacyBookmarks",
    "LegacyMUC",
    "LegacyContact",
    "LegacyParticipant",
    "LegacyRoster",
    "MucType",
    "FormField",
    "SearchResult",
    "user_store",
    "global_config",
]

addLoggingLevel()
