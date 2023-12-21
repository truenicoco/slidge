# Slixmpp: The Slick XMPP Library
# Copyright (C) 2012 Nathanael C. Fritz, Lance J.T. Stout
# This file is part of Slixmpp.
# See the file LICENSE for copying permission.
from slixmpp.plugins import BasePlugin

from . import stanza


class LinkPreview(BasePlugin):
    name = "link_preview"
    description = "Sender-generated link previews"
    dependencies = set()
    stanza = stanza

    def plugin_init(self):
        stanza.register_plugin()
