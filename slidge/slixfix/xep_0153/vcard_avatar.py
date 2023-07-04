# Slixmpp: The Slick XMPP Library
# Copyright (C) 2012 Nathanael C. Fritz, Lance J.T. Stout
# This file is part of Slixmpp.
# See the file LICENSE for copying permission.
import logging

from slixmpp.plugins.base import BasePlugin
from slixmpp.stanza import Presence
from slixmpp.xmlstream import register_stanza_plugin

from . import VCardTempUpdate, stanza

log = logging.getLogger(__name__)


class XEP_0153(BasePlugin):
    name = "xep_0153"
    description = "XEP-0153: vCard-Based Avatars (slidge, just for MUCs)"
    dependencies = {"xep_0054"}
    stanza = stanza

    def plugin_init(self):
        register_stanza_plugin(Presence, VCardTempUpdate)
