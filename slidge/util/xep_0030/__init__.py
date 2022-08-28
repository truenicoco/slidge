
# Slixmpp: The Slick XMPP Library
# Copyright (C) 2010 Nathanael C. Fritz, Lance J.T. Stout
# This file is part of Slixmpp.
# See the file LICENSE for copying permission.
from slixmpp.plugins.base import register_plugin

from . import stanza
from .disco import XEP_0030
from .stanza import DiscoInfo, DiscoItems
from .static import StaticDisco

register_plugin(XEP_0030)
