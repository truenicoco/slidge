import slixmpp.plugins

from .core import config as global_config
from .core.contact import LegacyContact, LegacyRoster
from .core.gateway import BaseGateway
from .core.muc import LegacyBookmarks, LegacyMUC, LegacyParticipant, MucType
from .core.session import BaseSession
from .util import (
    FormField,
    SearchResult,
    xep_0030,
    xep_0050,
    xep_0077,
    xep_0100,
    xep_0292,
    xep_0356,
    xep_0356_old,
    xep_0461,
)
from .util.db import GatewayUser, user_store

slixmpp.plugins.__all__.extend(["xep_0292_provider", "xep_0356_old", "xep_0461"])

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
