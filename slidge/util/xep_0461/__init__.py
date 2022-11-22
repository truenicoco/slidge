from slixmpp.plugins.base import register_plugin

from . import stanza
from .reply import XEP_0461

register_plugin(XEP_0461)
