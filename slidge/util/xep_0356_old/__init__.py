from slixmpp.plugins.base import register_plugin

from . import stanza
from .privilege import XEP_0356_OLD
from .stanza import PermOld, PrivilegeOld

register_plugin(XEP_0356_OLD)
