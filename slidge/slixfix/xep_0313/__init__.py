# Slixmpp: The Slick XMPP Library
# Copyright (C) 2012 Nathanael C. Fritz, Lance J.T. Stout
# This file is part of Slixmpp.
# See the file LICENSE for copying permission
from slixmpp.plugins.base import register_plugin

from .mam import XEP_0313
from .stanza import MAM, Metadata, Result

register_plugin(XEP_0313)

__all__ = ["XEP_0313", "Result", "MAM", "Metadata"]
