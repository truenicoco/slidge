
# Slixmpp: The Slick XMPP Library
# Copyright (C) 2012 Nathanael C. Fritz, Lance J.T. Stout
# This file is part of Slixmpp.
# See the file LICENSE for copying permission.
from slixmpp.plugins.base import register_plugin

from . import stanza
from .avatar import XEP_0084
from .stanza import Data, MetaData

register_plugin(XEP_0084)
