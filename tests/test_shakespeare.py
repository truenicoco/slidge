import logging
from copy import copy
from typing import Hashable, Optional, Dict, Any

from slixmpp import JID, Presence, Message
from slixmpp.exceptions import XMPPError

from slidge import *

from slidge.util.test import SlidgeTest
from slidge.core.contact import LegacyContactType
from slidge.util.types import LegacyMessageType

received_presences: list[Optional[Presence]] = []
text_received_by_juliet = []
composing_chat_states_received_by_juliet = []
unregistered = []
reactions_received_by_juliet = []


class Gateway(BaseGateway):
    COMPONENT_NAME = "SLIDGE TEST"

    async def unregister(self, user: GatewayUser):
        unregistered.append(user)


class Session(BaseSession):
    async def paused(self, c: LegacyContactType):
        pass

    async def correct(self, text: str, legacy_msg_id: Any, c: LegacyContactType):
        pass

    async def search(self, form_values: Dict[str, str]):
        pass

    def __init__(self, user):
        super().__init__(user)

    async def login(self):
        pass

    async def logout(self):
        pass

    async def send_text(self, t: str, c: LegacyContact, *, reply_to_msg_id=None):
        text_received_by_juliet.append((t, c))
        assert self.user.bare_jid == "romeo@montague.lit"
        assert self.user.jid == JID("romeo@montague.lit")
        if c.jid_username != "juliet":
            raise XMPPError(text="Not found", condition="item-not-found")
        else:
            c.send_text("I love you")
            return 0

    async def send_file(self, u: str, c: LegacyContact, *, reply_to_msg_id=None):
        pass

    async def active(self, c: LegacyContact):
        pass

    async def inactive(self, c: LegacyContact):
        pass

    async def composing(self, c: LegacyContact):
        composing_chat_states_received_by_juliet.append(c)

    async def displayed(self, legacy_msg_id: Hashable, c: LegacyContact):
        pass

    async def react(self, legacy_msg_id: LegacyMessageType, emojis: list[str], c: LegacyContact):
        if c.jid_username == "juliet":
            for e in emojis:
                reactions_received_by_juliet.append([legacy_msg_id, e])


class Roster(LegacyRoster):
    @staticmethod
    def jid_username_to_legacy_id(jid_username: str) -> int:
        log.debug("Requested JID to legacy: %s", jid_username)
        if jid_username == "juliet":
            return 123
        else:
            raise XMPPError(text="Not found", condition="item-not-found")

    @staticmethod
    def legacy_id_to_jid_username(legacy_id: int) -> str:
        if legacy_id == 123:
            return "juliet"
        else:
            raise RuntimeError


class TestAimShakespeareBase(SlidgeTest):
    plugin = globals()

    def setUp(self):
        super().setUp()
        user_store.add(
            JID("romeo@montague.lit/gajim"), {"username": "romeo", "city": ""}
        )

    def test_from_romeo_to_eve(self):
        self.recv(
            """
            <message type='chat'
                     to='eve@aim.shakespeare.lit'
                     from='romeo@montague.lit'>
                <body>Art thou not Romeo, and a Montague?</body>
            </message>
            """
        )
        s = self.next_sent()
        assert s["error"]["condition"] == "item-not-found"

    def test_from_romeo_to_juliet(self):
        self.recv(
            """
            <message type='chat'
                     to='juliet@aim.shakespeare.lit'
                     from='romeo@montague.lit'>
                <body>Art thou not Romeo, and a Montague?</body>
            </message>
            """
        )
        assert len(text_received_by_juliet) == 1
        text, contact = text_received_by_juliet[-1]
        assert text == "Art thou not Romeo, and a Montague?"
        assert contact.legacy_id == 123
        m: Message = self.next_sent()
        assert m.get_from() == "juliet@aim.shakespeare.lit/slidge"
        assert m["body"] == "I love you"
        m2 = copy(m)  # there must be a better way to check for the presence of the markable thing
        m2.enable("markable")
        assert m == m2

    def test_romeo_composing(self):
        self.recv(
            """
            <message type='chat'
                     to='juliet@aim.shakespeare.lit'
                     from='romeo@montague.lit'>
                <composing xmlns='http://jabber.org/protocol/chatstates'/>
            </message>
            """
        )
        assert len(composing_chat_states_received_by_juliet) == 1
        assert composing_chat_states_received_by_juliet[0].legacy_id == 123

    def test_from_eve_to_juliet(self):
        # just ignore messages from unregistered users
        self.recv(
            """
            <message type='chat'
                     from='eve@aim.shakespeare.lit'
                     to='juliet@montague.lit'>
                <body>Art thou not Romeo, and a Montague?</body>
            </message>
            """
        )
        self.send(None)

    def test_juliet_sends_text(self):
        session = BaseSession.get_self_or_unique_subclass().from_jid(
            JID("romeo@montague.lit")
        )
        juliet = session.contacts.by_jid(JID("juliet@aim.shakespeare.lit"))
        msg = juliet.send_text(body="What what?")

        # msg = self.next_sent()
        #  ^ this would be better but works when the test is run alone and fails
        # when all tests are run at once...

        assert msg["from"] == f"juliet@aim.shakespeare.lit/{LegacyContact.RESOURCE}"
        assert msg["to"] == "romeo@montague.lit"
        assert msg["body"] == "What what?"

    def test_unregister(self):
        assert len(unregistered) == 0
        self.recv(
            """
            <message type='chat'
                     to='juliet@aim.shakespeare.lit'
                     from='romeo@montague.lit'>
                <composing xmlns='http://jabber.org/protocol/chatstates'/>
            </message>
            """
        )  # this creates a session
        self.recv(
            """
            <iq from='romeo@montague.lit' type='set' to='aim.shakespeare.lit'>
              <query xmlns='jabber:iq:register'>
                <remove />
              </query>
            </iq>
            """
        )
        assert len(unregistered) == 1
        assert unregistered[0].jid == "romeo@montague.lit"

    def test_jid_validator(self):
        self.recv(
            """
            <iq from='eve@nothingshakespearian' type='get' to='aim.shakespeare.lit'>
              <query xmlns='jabber:iq:register'>
              </query>
            </iq>
            """
        )
        assert self.next_sent()["error"]["condition"] == "not-allowed"
        self.recv(
            """
            <iq from='eve@nothingshakespearian' type='set' to='aim.shakespeare.lit'>
              <query xmlns='jabber:iq:register'>
                <username>bill</username>
                <password>Calliope</password>
               </query>
            </iq>
            """
        )
        assert self.next_sent()["error"]["condition"] == "not-allowed"

    def test_reactions(self):
        self.recv(
            """
            <message type='chat'
                     to='juliet@aim.shakespeare.lit'
                     from='romeo@montague.lit'>
              <reactions id='xmpp-id1' xmlns='urn:xmpp:reactions:0'>
                <reaction>👋</reaction>
                <reaction>🐢</reaction>
              </reactions>
            </message>
            """
        )
        assert len(reactions_received_by_juliet) == 2
        msg_id, emoji = reactions_received_by_juliet[0]
        assert msg_id == "xmpp-id1"
        assert emoji == "👋"
        msg_id, emoji = reactions_received_by_juliet[1]
        assert msg_id == "xmpp-id1"
        assert emoji == "🐢"

        session = BaseSession.get_self_or_unique_subclass().from_jid(
            JID("romeo@montague.lit")
        )
        juliet = session.contacts.by_jid(JID("juliet@aim.shakespeare.lit"))
        msg = juliet.react("legacy1", "👋")
        assert msg["reactions"]["id"] == "legacy1"
        for r in msg["reactions"]:
            assert r["value"] == "👋"


log = logging.getLogger(__name__)
