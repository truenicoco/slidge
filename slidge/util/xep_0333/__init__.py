# slixmpp: The Slick XMPP Library
# Copyright (C) 2016 Emmanuel Gil Peyrot
# This file is part of slixmpp.
# See the file LICENSE for copying permission.
from slixmpp.plugins.base import register_plugin

from .markers import XEP_0333
from .stanza import Acknowledged, Displayed, Markable, Received

register_plugin(XEP_0333)
