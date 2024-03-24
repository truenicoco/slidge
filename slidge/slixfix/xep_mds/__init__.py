from slixmpp.plugins.base import register_plugin

from . import stanza
from .mds import XEP_xxxx_mds

register_plugin(XEP_xxxx_mds)

__all__ = ["stanza", "XEP_xxxx_mds"]
