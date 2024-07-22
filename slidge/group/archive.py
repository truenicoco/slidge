import logging
import uuid
from copy import copy
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Collection, Optional

from slixmpp import Iq, Message

from ..db.models import ArchivedMessage, ArchivedMessageSource
from ..db.store import MAMStore
from ..util.archive_msg import HistoryMessage
from ..util.types import HoleBound

if TYPE_CHECKING:
    from .participant import LegacyParticipant


class MessageArchive:
    def __init__(self, room_pk: int, store: MAMStore):
        self.room_pk = room_pk
        self.__store = store

    def add(
        self,
        msg: Message,
        participant: Optional["LegacyParticipant"] = None,
        archive_only=False,
        legacy_msg_id=None,
    ):
        """
        Add a message to the archive if it is deemed archivable

        :param msg:
        :param participant:
        :param archive_only:
        :param legacy_msg_id:
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
                new_msg["muc"]["jid"] = participant.user_jid.bare
            elif participant.is_system:
                new_msg["muc"]["jid"] = participant.muc.jid
            else:
                log.warning("No real JID for participant in this group")
                new_msg["muc"][
                    "jid"
                ] = f"{uuid.uuid4()}@{participant.xmpp.boundjid.bare}"

        self.__store.add_message(
            self.room_pk,
            HistoryMessage(new_msg),
            archive_only,
            None if legacy_msg_id is None else str(legacy_msg_id),
        )

    def __iter__(self):
        return iter(self.get_all())

    @staticmethod
    def __to_bound(stored: ArchivedMessage):
        return HoleBound(
            stored.legacy_id,  # type:ignore
            stored.timestamp.replace(tzinfo=timezone.utc),
        )

    def get_hole_bounds(self) -> tuple[HoleBound | None, HoleBound | None]:
        most_recent = self.__store.get_most_recent_with_legacy_id(self.room_pk)
        if most_recent is None:
            return None, None
        if most_recent.source == ArchivedMessageSource.BACKFILL:
            # most recent = only backfill, fetch everything since last backfill
            return self.__to_bound(most_recent), None

        most_recent_back_filled = self.__store.get_most_recent_with_legacy_id(
            self.room_pk, ArchivedMessageSource.BACKFILL
        )
        if most_recent_back_filled is None:
            # group was never back-filled, fetch everything before first live
            least_recent_live = self.__store.get_first(self.room_pk, True)
            assert least_recent_live is not None
            return None, self.__to_bound(least_recent_live)

        assert most_recent_back_filled.legacy_id is not None
        least_recent_live = self.__store.get_least_recent_with_legacy_id_after(
            self.room_pk, most_recent_back_filled.legacy_id
        )
        assert least_recent_live is not None
        # this is a hole caused by slidge downtime
        return self.__to_bound(most_recent_back_filled), self.__to_bound(
            least_recent_live
        )

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
        for msg in self.__store.get_messages(
            self.room_pk,
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
        messages = self.__store.get_first_and_last(self.room_pk)
        if messages:
            for x, m in [("start", messages[0]), ("end", messages[-1])]:
                reply["mam_metadata"][x]["id"] = m.id
                reply["mam_metadata"][x]["timestamp"] = m.sent_on.replace(
                    tzinfo=timezone.utc
                )
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
