# slixmpp: The Slick XMPP Library
# Copyright (C) 2018 Emmanuel Gil Peyrot
# This file is part of slixmpp.
# See the file LICENSE for copying permission.
from slixmpp.plugins.base import register_plugin

from .http_upload import (
    XEP_0363,
    FileTooBig,
    FileUploadError,
    HTTPError,
    UploadServiceNotFound,
)
from .stanza import Get, Header, Put, Request, Slot

register_plugin(XEP_0363)
