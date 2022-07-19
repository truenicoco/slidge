from slixmpp.plugins.base import register_plugin

from .gateway import XEP_0100, LegacyError

register_plugin(XEP_0100)
