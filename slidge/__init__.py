from . import xep_0055
from . import xep_0077
from . import xep_0100
from . import xep_0115
from . import xep_0333
from . import xep_0356
from . import xep_0363

import slixmpp.plugins

from .db import user_store, GatewayUser
from .gateway import BaseGateway
from .util import FormField, SearchResult
from .legacy.session import BaseSession
from .legacy.contact import LegacyContact, LegacyRoster

slixmpp.plugins.__all__.extend(["xep_0055", "xep_0356"])

__all__ = [
    "BaseGateway",
    "BaseSession",
    "GatewayUser",
    "LegacyContact",
    "LegacyRoster",
    "FormField",
    "SearchResult",
    "user_store",
]
