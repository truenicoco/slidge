import logging
import uuid
from copy import copy
from datetime import datetime
from typing import TYPE_CHECKING, Collection, Optional

from slixmpp import Iq, Message

from ..util.archive_msg import HistoryMessage
from ..util.db import GatewayUser
from ..util.sql import db

if TYPE_CHECKING:
    from .participant import LegacyParticipant


class MessageArchive:
    def __init__(self, db_id: str, user: GatewayUser):
        self.db_id = db_id
        self.user = user
        db.mam_add_muc(db_id, user)

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
        if participant and not participant.muc.is_anonymous:
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

        db.mam_add_msg(self.db_id, HistoryMessage(new_msg), self.user)

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
        for msg in db.mam_get_messages(
            self.user,
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
            yield msg

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
                reply["mam_metadata"][x]["id"] = m.id
                reply["mam_metadata"][x]["timestamp"] = m.sent_on
        else:
            reply.enable("mam_metadata")
        reply.send()


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


log = logging.getLogger(__name__)
