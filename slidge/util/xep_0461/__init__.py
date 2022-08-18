from slixmpp.plugins.base import register_plugin

from .reply import XEP_0461
from .stanza import *

register_plugin(XEP_0461)
