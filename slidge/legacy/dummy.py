import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

from slixmpp import Message

from slidge.base_legacy import LegacyError, BaseLegacyClient
from slidge.database import User
from slidge.buddy import Buddy
from slidge.session import sessions
from slidge.gateway import BaseGateway as Gateway
from slidge.muc import LegacyMuc

assets_path = Path(__file__).parent.parent.parent / "assets"


class MockLegacyClient(BaseLegacyClient):
    buddy1 = Buddy("buddy1")
    with (assets_path / "buddy1.png").open("rb") as fp:
        buddy1.avatar_bytes = fp.read()
    buddy2 = Buddy("buddy2")
    with (assets_path / "buddy2.png").open("rb") as fp:
        buddy2.avatar_bytes = fp.read()
    buddies = [buddy1, buddy2]

    legacy_sent = []

    muc = LegacyMuc(legacy_id="GrOuP")
    occupants = ["participant1", "participant2", "participant3"]

    @property
    def last_sent(self):
        return self.legacy_sent[-1]

    @last_sent.setter
    def last_sent(self, value):
        self.legacy_sent.append(value)

    async def validate(self, ifrom, reg):
        if reg["username"] == "invalid":
            raise ValueError

    async def get_buddies(self, user):
        return self.buddies

    async def send_message(self, user, legacy_buddy_id: str, msg: Message):
        self.legacy_sent.append(
            {"from": user, "to": legacy_buddy_id, "msg": msg, "type": "1on1"},
        )
        log.debug(f"Send queue: {id(self.legacy_sent)}, {self.legacy_sent}")

        session = sessions.by_legacy_id(user.legacy_id)
        buddy = session.buddies.by_legacy_id(legacy_buddy_id)
        log.debug(f"Sessions xmpp {sessions.xmpp}")
        if msg["body"] == "invalid":
            raise LegacyError("didn't work")
        elif msg["body"] == "carbon":
            buddy.send_xmpp_carbon(
                "I sent this from the official client",
                timestamp=datetime.now() - timedelta(hours=1),
            )
        elif msg["body"] == "away":
            buddy.ptype = "away"
        else:
            log.debug("Acking")
            buddy.send_xmpp_ack(msg)
            log.debug("Reading")
            buddy.send_xmpp_read(msg)
            log.debug("Composing")
            buddy.send_xmpp_composing()
            await asyncio.sleep(2)
            log.debug("Sending")
            buddy.send_xmpp_message("I got that")

    async def send_receipt(self, user: User, receipt: Message):
        log.debug("I sent a receipt")
        self.last_sent = {"user": user, "receipt": receipt, "type": "receipt"}

    async def send_composing(self, user: User, legacy_buddy_id: str):
        log.debug("I sent composing")
        self.last_sent = {"user": user, "to": legacy_buddy_id, "type": "composing"}

    async def send_pause(self, user: User, legacy_buddy_id: str):
        log.debug("I sent pause")
        self.last_sent = {"user": user, "to": legacy_buddy_id, "type": "pause"}

    async def send_read_mark(self, user: User, legacy_buddy_id: str, msg_id: str):
        log.debug("I sent read")
        self.last_sent = {"user": user, "to": legacy_buddy_id, "type": "read_mark"}

    async def send_muc_message(self, user: User, legacy_group_id: str, msg: Message):
        self.last_sent = {"user": user, "to": legacy_group_id, "type": "group_msg"}
        session = sessions.by_legacy_id(user.legacy_id)
        muc = session.mucs.by_legacy_id(legacy_group_id)
        muc.to_user("ghost", "I'm not here")

    async def muc_list(self, user: User):
        return [self.muc]

    async def muc_occupants(self, user: User, legacy_group_id: str):
        return self.occupants


log = logging.getLogger(__name__)
