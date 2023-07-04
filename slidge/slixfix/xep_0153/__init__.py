# Slixmpp: The Slick XMPP Library
# Copyright (C) 2012 Nathanael C. Fritz, Lance J.T. Stout
# This file is part of Slixmpp.
# See the file LICENSE for copying permission.
from slixmpp.plugins.base import register_plugin

from .stanza import VCardTempUpdate
from .vcard_avatar import XEP_0153

register_plugin(XEP_0153)
