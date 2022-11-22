import asyncio
import datetime
import logging
from copy import copy
from typing import Hashable, Optional, Dict, Any

from slixmpp import JID, Presence, Message
from slixmpp.exceptions import XMPPError

from slidge import *

from slidge.util.test import SlidgeTest
from slidge.core.contact import LegacyContactType
from slidge.util.types import LegacyMessageType
from slidge.util.xep_0356.permissions import (
    Permissions,
    MessagePermission,
    PresencePermission,
    RosterAccess,
)
from slidge.core import config
from slidge.core import session


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

    async def send_text(
        self,
        t: str,
        c: LegacyContact,
        *,
        reply_to_msg_id=None,
        reply_to_fallback_text: Optional[str] = None,
    ):
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

    async def react(
        self, legacy_msg_id: LegacyMessageType, emojis: list[str], c: LegacyContact
    ):
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
        m2 = copy(
            m
        )  # there must be a better way to check for the presence of the markable thing
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

    def test_last_seen(self):
        session = BaseSession.get_self_or_unique_subclass().from_jid(
            JID("romeo@montague.lit")
        )
        juliet = session.contacts.by_jid(JID("juliet@aim.shakespeare.lit"))
        now = datetime.datetime.now(datetime.timezone.utc)
        juliet.away(last_seen=now)
        sent = self.next_sent()
        assert sent["idle"]["since"] == now


class TestNameSquatting(SlidgeTest):
    plugin = globals()

    def setUp(self):
        super().setUp()

        async def login(*args, **kwargs):
            raise RuntimeError

        self.original_login = Session.login
        Session.login = login
        Gateway.REGISTRATION_MULTISTEP = True
        config.PARTIAL_REGISTRATION_TIMEOUT = 1

    def tearDown(self):
        Gateway.REGISTRATION_MULTISTEP = False
        config.PARTIAL_REGISTRATION_TIMEOUT = 3600
        Session.login = self.original_login

    def test_name_squatting(self):
        async def sleep():
            await asyncio.sleep(3)

        self.recv(
            """
            <iq from="bard@shakespeare.lit" type='set' id='reg2'>
              <query xmlns='jabber:iq:register'>
                <username>bill</username>
                <password>Calliope</password>
                <email>bard@shakespeare.lit</email>
              </query>
            </iq>
            """
        )
        self.send(
            """
            <iq type='result' id='reg2' to="bard@shakespeare.lit"/>
            """
        )
        user = user_store.get(None, None, JID("bard@shakespeare.lit"), None)
        assert user is not None
        assert user in session._sessions
        self.xmpp.loop.run_until_complete(sleep())
        assert user_store.get(None, None, JID("bard@shakespeare.lit"), None) is None
        assert user not in session._sessions


class TestPrivilegeOld(SlidgeTest):
    plugin = globals()

    def setUp(self):
        super().setUp()
        user_store.add(
            JID("romeo@shakespeare.lit/gajim"), {"username": "romeo", "city": ""}
        )

    def test_privilege_old(self):
        assert (
            self.xmpp["xep_0356"].granted_privileges["shakespeare.lit"] == Permissions()
        )
        assert (
            self.xmpp["xep_0356_old"].granted_privileges["shakespeare.lit"]
            == Permissions()
        )
        self.recv(
            """
            <message to="aim.shakespeare.lit" from="shakespeare.lit">
              <privilege xmlns="urn:xmpp:privilege:1">
                <perm access="roster" type="both" />
                <perm access="message" type="outgoing" />
              </privilege>
            </message>
            """
        )
        assert (
            self.xmpp["xep_0356_old"].granted_privileges["shakespeare.lit"].message
            == MessagePermission.OUTGOING
        )
        assert (
            self.xmpp["xep_0356_old"].granted_privileges["shakespeare.lit"].presence
            == PresencePermission.NONE
        )
        assert (
            self.xmpp["xep_0356_old"].granted_privileges["shakespeare.lit"].roster
            == RosterAccess.BOTH
        )

        session = BaseSession.get_self_or_unique_subclass().from_jid(
            JID("romeo@shakespeare.lit")
        )
        juliet = session.contacts.by_jid(JID("juliet@aim.shakespeare.lit"))
        juliet.carbon("body")
        self.send(
            """
            <message to="shakespeare.lit" from="aim.shakespeare.lit">
              <privilege xmlns="urn:xmpp:privilege:1">
                <forwarded xmlns="urn:xmpp:forward:0">
                  <message xmlns="jabber:client" to="juliet@aim.shakespeare.lit" type="chat" from="romeo@shakespeare.lit">
                    <body>body</body>
                    <store xmlns="urn:xmpp:hints" />
                  </message>
                </forwarded>
              </privilege>
            </message>
            """,
        )
        self.xmpp.loop.create_task(juliet.add_to_roster())
        self.send(
            """
            <iq xmlns="jabber:component:accept" type="set" to="romeo@shakespeare.lit" from="aim.shakespeare.lit" id="1">
              <query xmlns="jabber:iq:roster">
                <item subscription="both" jid="juliet@aim.shakespeare.lit">
                  <group>slidge</group>
                </item>
              </query>
            </iq>
            """
        )


class TestPrivilege(SlidgeTest):
    plugin = globals()

    def setUp(self):
        super().setUp()
        user_store.add(
            JID("romeo@shakespeare.lit/gajim"), {"username": "romeo", "city": ""}
        )

    def test_privilege_old(self):
        assert (
            self.xmpp["xep_0356"].granted_privileges["shakespeare.lit"] == Permissions()
        )
        assert (
            self.xmpp["xep_0356_old"].granted_privileges["shakespeare.lit"]
            == Permissions()
        )
        self.recv(
            """
            <message to="aim.shakespeare.lit" from="shakespeare.lit">
              <privilege xmlns="urn:xmpp:privilege:2">
                <perm access="roster" type="both" />
                <perm access="message" type="outgoing" />
              </privilege>
            </message>
            """
        )
        assert (
            self.xmpp["xep_0356"].granted_privileges["shakespeare.lit"].message
            == MessagePermission.OUTGOING
        )
        assert (
            self.xmpp["xep_0356"].granted_privileges["shakespeare.lit"].presence
            == PresencePermission.NONE
        )
        assert (
            self.xmpp["xep_0356"].granted_privileges["shakespeare.lit"].roster
            == RosterAccess.BOTH
        )

        session = BaseSession.get_self_or_unique_subclass().from_jid(
            JID("romeo@shakespeare.lit")
        )
        juliet = session.contacts.by_jid(JID("juliet@aim.shakespeare.lit"))
        juliet.carbon("body")
        self.send(
            """
            <message to="shakespeare.lit" from="aim.shakespeare.lit">
              <privilege xmlns="urn:xmpp:privilege:2">
                <forwarded xmlns="urn:xmpp:forward:0">
                  <message xmlns="jabber:client" to="juliet@aim.shakespeare.lit" type="chat" from="romeo@shakespeare.lit">
                    <body>body</body>
                    <store xmlns="urn:xmpp:hints" />
                  </message>
                </forwarded>
              </privilege>
            </message>
            """,
        )
        self.xmpp.loop.create_task(juliet.add_to_roster())
        self.send(
            """
            <iq xmlns="jabber:component:accept" type="set" to="romeo@shakespeare.lit" from="aim.shakespeare.lit" id="1">
              <query xmlns="jabber:iq:roster">
                <item subscription="both" jid="juliet@aim.shakespeare.lit">
                  <group>slidge</group>
                </item>
              </query>
            </iq>
            """
        )


log = logging.getLogger(__name__)
