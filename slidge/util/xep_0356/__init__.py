from slixmpp.plugins.base import register_plugin

from . import stanza
from .stanza import Perm, Privilege
from .privilege import XEP_0356

register_plugin(XEP_0356)
