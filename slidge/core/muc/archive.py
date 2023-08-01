import logging
import uuid
from copy import copy
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Collection, Optional, Union
from xml.etree import ElementTree as ET

from slixmpp import Iq, Message
from slixmpp.plugins.xep_0297.stanza import Forwarded

from ...util.sql import db
from ...util.types import MucType

if TYPE_CHECKING:
    from .participant import LegacyParticipant


class MessageArchive:
    def __init__(self, db_id: str, retention_days: Optional[int] = None):
        self.db_id = db_id
        db.mam_add_muc(db_id)
        self._retention = retention_days

    def add(
        self,
        msg: Message,
        participant: Optional["LegacyParticipant"] = None,
    ):
        """
        Add a message to the archive if it is deemed archivable

        :param msg:
        :param participant:
        """
        if not archivable(msg):
            return
        new_msg = copy(msg)
        if participant and participant.muc.type == MucType.GROUP:
            new_msg["muc"]["role"] = participant.role
            new_msg["muc"]["affiliation"] = participant.affiliation
            if participant.contact:
                new_msg["muc"]["jid"] = participant.contact.jid.bare
            elif participant.is_user:
                new_msg["muc"]["jid"] = participant.user.jid.bare
            elif participant.is_system:
                new_msg["muc"]["jid"] = participant.muc.jid
            else:
                log.warning("No real JID for participant in this group")
                new_msg["muc"][
                    "jid"
                ] = f"{uuid.uuid4()}@{participant.xmpp.boundjid.bare}"

        db.mam_add_msg(self.db_id, HistoryMessage(new_msg))
        if self._retention:
            db.mam_clean_history(self.db_id, self._retention)

    def __iter__(self):
        return iter(self.get_all())

    def get_all(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        before_id: Optional[str] = None,
        after_id: Optional[str] = None,
        ids: Collection[str] = (),
        last_page_n: Optional[int] = None,
        sender: Optional[str] = None,
        flip=False,
    ):
        for row in db.mam_get_messages(
            self.db_id,
            before_id=before_id,
            after_id=after_id,
            ids=ids,
            last_page_n=last_page_n,
            sender=sender,
            start_date=start_date,
            end_date=end_date,
            flip=flip,
        ):
            yield HistoryMessage(
                row[0], when=datetime.fromtimestamp(row[1], tz=timezone.utc)
            )

    async def send_metadata(self, iq: Iq):
        """
        Send archive extent, as per the spec

        :param iq:
        :return:
        """
        reply = iq.reply()
        messages = db.mam_get_first_and_last(self.db_id)
        if messages:
            for x, m in [("start", messages[0]), ("end", messages[-1])]:
                reply["mam_metadata"][x]["id"] = m[0]
                reply["mam_metadata"][x]["timestamp"] = datetime.fromtimestamp(
                    m[1], tz=timezone.utc
                )
        else:
            reply.enable("mam_metadata")
        reply.send()


class HistoryMessage:
    def __init__(self, stanza: Union[Message, str], when: Optional[datetime] = None):
        if isinstance(stanza, str):
            from_db = True
            stanza = Message(xml=ET.fromstring(stanza))
        else:
            from_db = False

        self.id = stanza["stanza_id"]["id"]
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


def archivable(msg: Message):
    """
    Determine if a message stanza is worth archiving, ie, convey meaningful
    info

    :param msg:
    :return:
    """

    if msg.get_plugin("hint", check=True) and msg["hint"] == "no-store":
        return False

    if msg["body"]:
        return True

    if msg.get_plugin("apply_to", check=True):
        # retractions
        return True

    if msg.get_plugin("reactions", check=True):
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


log = logging.getLogger(__name__)
