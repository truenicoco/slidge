import warnings

import slixmpp.plugins

from .core import config as global_config
from .core.command import FormField, SearchResult
from .core.contact import LegacyContact, LegacyRoster
from .core.gateway import BaseGateway
from .core.muc import LegacyBookmarks, LegacyMUC, LegacyParticipant, MucType
from .core.session import BaseSession
from .util import (  # noqa: F401
    xep_0030,
    xep_0050,
    xep_0054,
    xep_0077,
    xep_0100,
    xep_0153,
    xep_0234,
    xep_0292,
    xep_0313,
    xep_0356,
    xep_0356_old,
    xep_0372,
    xep_0402,
    xep_0446,
    xep_0447,
    xep_0461,
)
from .util.db import GatewayUser, user_store
from .util.error import XMPPError


def formatwarning(message, category, filename, lineno, line=""):
    return f"{filename}:{lineno}:{category.__name__}:{message}\n"


warnings.formatwarning = formatwarning


# TODO: (later) mv from .__all__ to .PLUGINS on the next release of slixmpp
slixmpp.plugins.__all__.extend(
    [
        "xep_0234",
        "xep_0292_provider",
        "xep_0356_old",
        "xep_0372",
        "xep_0385",
        "xep_0402",
        "xep_0461",
        "xep_0446",
        "xep_0447",
    ]
)

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
    "XMPPError",
    "user_store",
    "global_config",
]
