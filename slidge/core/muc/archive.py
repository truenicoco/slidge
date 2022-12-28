from copy import copy
from datetime import datetime, timezone
from typing import Collection, Optional

from slixmpp import Iq, Message
from slixmpp.exceptions import XMPPError
from slixmpp.plugins.xep_0297.stanza import Forwarded
from slixmpp.plugins.xep_0444.stanza import NS as ReactionsNameSpace


class MessageArchive:
    def __init__(self):
        self._msg_by_ids = dict[str, HistoryMessage]()
        self._msgs = list[HistoryMessage]()

    def add(self, msg: Message):
        """
        Add a message to the archive if it is deemed archivable

        :param msg:
        """
        if not archivable(msg):
            return

        archived = HistoryMessage(msg)
        self._msgs.append(archived)
        self._msg_by_ids[archived.id] = archived

    def get_all(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        before_id: Optional[str] = None,
        after_id: Optional[str] = None,
        ids: Collection[str] = (),
        last_page_n: Optional[int] = None,
        sender: Optional[str] = None,
    ):
        """
        Very unoptimized archive fetching

        :param start_date:
        :param end_date:
        :param before_id:
        :param after_id:
        :param ids:
        :param last_page_n:
        :param sender:
        :return:
        """
        for i in [before_id, after_id] + list(ids):
            if i is not None and i not in self._msg_by_ids:
                raise XMPPError("item-not-found")

        if last_page_n:
            messages = self._msgs[-last_page_n:]
        else:
            messages = self._msgs

        found_after_id = False
        for history_msg in messages:
            if sender and history_msg.stanza.get_from() != sender:
                continue
            if start_date and history_msg.when < start_date:
                continue
            if end_date and history_msg.when > end_date:
                continue
            if before_id and before_id == history_msg.id:
                break
            if after_id:
                if history_msg.id == after_id:
                    found_after_id = True
                    continue
                elif not found_after_id:
                    continue
            if ids and history_msg.id not in ids:
                continue

            yield history_msg

    async def send_metadata(self, iq: Iq):
        """
        Send archive extent, as per the spec

        :param iq:
        :return:
        """
        reply = iq.reply()
        if self._msgs:
            for x, m in [("start", self._msgs[0]), ("end", self._msgs[-1])]:
                reply["mam_metadata"][x]["id"] = m.id
                reply["mam_metadata"][x]["timestamp"] = m.when
        else:
            reply.enable("mam_metadata")
        reply.send()


class HistoryMessage:
    def __init__(self, stanza: Message):
        stanza = copy(stanza)

        self.id = stanza["stanza_id"]["id"]
        self.when = stanza["delay"]["stamp"] or datetime.now(tz=timezone.utc)

        del stanza["delay"]
        del stanza["markable"]
        del stanza["hint"]
        del stanza["chat_state"]

        self.stanza_component_ns = stanza
        stanza = copy(stanza)
        fix_namespaces(stanza.xml)
        self.stanza = stanza

    def forwarded(self):
        forwarded = Forwarded()
        forwarded["delay"]["stamp"] = self.when
        forwarded.append(self.stanza)
        return forwarded


def archivable(msg: Message):
    """
    Determine if a message stanza is worth archiving, ie, convey meaningful
    info

    :param msg:
    :return:
    """
    if msg["body"]:
        return True

    if msg.xml.find("{urn:xmpp:fasten:0}apply-to") is not None:
        # retractions
        return True

    if msg.xml.find(f"{{{ReactionsNameSpace}}}reactions") is not None:
        return True

    return False


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
