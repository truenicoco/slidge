from copy import copy
from datetime import datetime, timezone
from typing import Optional, Union
from uuid import uuid4
from xml.etree import ElementTree as ET

from slixmpp import Message
from slixmpp.plugins.xep_0297 import Forwarded


def fix_namespaces(xml, old="{jabber:component:accept}", new="{jabber:client}"):
    """
    Hack to fix namespaces between jabber:component and jabber:client

    Acts in-place.

    :param xml:
    :param old:
    :param new:
    """
    xml.tag = xml.tag.replace(old, new)
    for child in xml:
        fix_namespaces(child, old, new)


class HistoryMessage:
    def __init__(self, stanza: Union[Message, str], when: Optional[datetime] = None):
        if isinstance(stanza, str):
            from_db = True
            stanza = Message(xml=ET.fromstring(stanza))
        else:
            from_db = False

        self.id = stanza["stanza_id"]["id"] or uuid4().hex
        self.when: datetime = (
            when or stanza["delay"]["stamp"] or datetime.now(tz=timezone.utc)
        )

        if not from_db:
            del stanza["delay"]
            del stanza["markable"]
            del stanza["hint"]
            del stanza["chat_state"]
            if not stanza["body"]:
                del stanza["body"]
            fix_namespaces(stanza.xml)

        self.stanza: Message = stanza

    @property
    def stanza_component_ns(self):
        stanza = copy(self.stanza)
        fix_namespaces(
            stanza.xml, old="{jabber:client}", new="{jabber:component:accept}"
        )
        return stanza

    def forwarded(self):
        forwarded = Forwarded()
        forwarded["delay"]["stamp"] = self.when
        forwarded.append(self.stanza)
        return forwarded
