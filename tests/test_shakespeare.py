import datetime
import logging
import tempfile
from copy import copy
from pathlib import Path
from typing import Hashable, Optional, Dict, Any

from slixmpp import JID, Presence, Message
from slixmpp.exceptions import XMPPError
from slixmpp.plugins.xep_0082 import format_datetime

from slidge import *
from slidge.core.mixins.message import AttachmentMixin

from slidge.util.test import SlidgeTest
from slidge.util.types import LegacyMessageType, LegacyContactType
from slidge.util.xep_0356.permissions import (
    Permissions,
    MessagePermission,
    PresencePermission,
    RosterAccess,
)
from slidge.core import config


received_presences: list[Optional[Presence]] = []
text_received_by_juliet = []
composing_chat_states_received_by_juliet = []
unregistered = []
reactions_received_by_juliet = []


class Gateway(BaseGateway):
    COMPONENT_NAME = "SLIDGE TEST"

    SEARCH_FIELDS = [FormField(var="leg", label="Enter the legacy ID")]
    SEARCH_TITLE = "Search for legacy contacts"

    GROUPS = True

    async def unregister(self, user: GatewayUser):
        unregistered.append(user)


class Session(BaseSession):
    async def paused(self, c: LegacyContactType):
        pass

    async def correct(self, text: str, legacy_msg_id: Any, c: LegacyContactType):
        pass

    async def search(self, form_values: Dict[str, str]):
        if form_values["leg"] == "exists":
            return SearchResult(
                fields=[FormField(var="jid", label="JID", type="jid-single")],
                items=[{"jid": "exists@example.com"}],
            )

    def __init__(self, user):
        super().__init__(user)

    async def login(self):
        pass

    async def logout(self):
        pass

    async def send_text(
        self,
        text: str,
        chat: LegacyContact,
        *,
        reply_to=None,
        reply_to_msg_id=None,
        reply_to_fallback_text: Optional[str] = None,
    ):
        if chat.jid_username == "juliet":
            text_received_by_juliet.append((text, chat))
        assert self.user.bare_jid == "romeo@montague.lit"
        assert self.user.jid == JID("romeo@montague.lit")
        chat.send_text("I love you")
        return 0

    async def send_file(self, url: str, chat: LegacyContact, *, reply_to_msg_id=None):
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
    async def jid_username_to_legacy_id(jid_username: str) -> int:
        log.debug("Requested JID to legacy: %s", jid_username)
        if jid_username == "juliet":
            return 123
        else:
            raise XMPPError(text="Only juliet", condition="item-not-found")

    @staticmethod
    async def legacy_id_to_jid_username(legacy_id: int) -> str:
        if legacy_id == 123:
            return "juliet"
        else:
            raise RuntimeError


class Bookmarks(LegacyBookmarks):
    @staticmethod
    async def jid_local_part_to_legacy_id(local_part):
        if local_part != "room":
            raise XMPPError("not-found")
        else:
            return local_part


class TestAimShakespeareBase(SlidgeTest):
    plugin = globals()

    def setUp(self):
        super().setUp()
        user_store.add(
            JID("romeo@montague.lit/gajim"), {"username": "romeo", "city": ""}
        )
        self.get_romeo_session().logged = True

    @staticmethod
    def get_romeo_session() -> Session:
        return BaseSession.get_self_or_unique_subclass().from_jid(
            JID("romeo@montague.lit")
        )

    def test_jabber_iq_gateway(self):
        self.recv(
            """
            <iq type='get' to='aim.shakespeare.lit' from='romeo@montague.lit' id='gate1'>
              <query xmlns='jabber:iq:gateway'/>
            </iq>
            """
        )
        self.send(
            f"""
            <iq type='result' from='aim.shakespeare.lit' to='romeo@montague.lit' id='gate1'>
                <query xmlns='jabber:iq:gateway'>
                  <desc>{Gateway.SEARCH_TITLE}</desc>
                  <prompt>{Gateway.SEARCH_FIELDS[0].label}</prompt>
                </query>
            </iq>
            """
        )
        self.recv(
            """
            <iq type='set' to='aim.shakespeare.lit' from='romeo@montague.lit' id='gate1'>
              <query xmlns='jabber:iq:gateway'>
                <prompt>exists</prompt>
              </query>
            </iq>
            """
        )
        self.send(
            """
            <iq type='result' from='aim.shakespeare.lit' to='romeo@montague.lit' id='gate1'>
              <query xmlns='jabber:iq:gateway'>
                <jid>exists@example.com</jid>
              </query>
            </iq>
            """
        )
        self.recv(
            """
            <iq type='set' to='aim.shakespeare.lit' from='romeo@montague.lit' id='gate1'>
              <query xmlns='jabber:iq:gateway'>
                <prompt>not-exists</prompt>
              </query>
            </iq>
            """
        )
        self.send(
            """
            <iq type='error' from='aim.shakespeare.lit' to='romeo@montague.lit' id='gate1'>
              <error type="cancel">
                <item-not-found xmlns="urn:ietf:params:xml:ns:xmpp-stanzas"/>
                <text xmlns="urn:ietf:params:xml:ns:xmpp-stanzas">No contact was found with the info you provided.</text>
              </error>
            </iq>
            """,
            use_values=False,
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
        self.send(
            """
            <message type="error" to="romeo@montague.lit" from="eve@aim.shakespeare.lit">
                <error type="cancel"><item-not-found xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
                <text xmlns="urn:ietf:params:xml:ns:xmpp-stanzas">Only juliet
            </text></error></message>
            """,
            use_values=False
        )

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

    def test_delivery_receipt(self):
        self.recv(
            """
            <message type='chat'
                     to='juliet@aim.shakespeare.lit/slidge'
                     from='romeo@montague.lit/prout'
                     id="123">
                <body>Art thou not Romeo, and a Montague?</body>
                <request xmlns='urn:xmpp:receipts'/>
            </message>
            """
        )
        self.next_sent()  # auto reply in our test plugin
        self.send(
            """
            <message xmlns="jabber:component:accept"
                    type="chat"
                    to="romeo@montague.lit/prout"
                    from="juliet@aim.shakespeare.lit/slidge">
   	            <received xmlns="urn:xmpp:receipts" id="123"/>
            </message>
            """
        )
        self.recv(
            """
            <message type='chat'
                     to='juliet@aim.shakespeare.lit/slidge'
                     from='romeo@montague.lit/prout'
                     id="123">
                <body>Art thou not Romeo, and a Montague?</body>
            </message>
            """
        )
        self.next_sent()  # auto reply in our test plugin
        assert self.next_sent() is None

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
        juliet = self.xmpp.loop.run_until_complete(
            session.contacts.by_jid(JID("juliet@aim.shakespeare.lit"))
        )
        juliet.send_text(body="What what?")

        msg = self.next_sent()

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
            <iq from='eve@nothingshakespearian' type='get' to='aim.shakespeare.lit' id="0">
              <query xmlns='jabber:iq:register'>
              </query>
            </iq>
            """
        )
        self.send(
            """
           <iq xmlns="jabber:component:accept" from="aim.shakespeare.lit" type="error" to="eve@nothingshakespearian" id="0">
            <error type="cancel">
                <not-allowed xmlns="urn:ietf:params:xml:ns:xmpp-stanzas"/>
                <text xmlns="urn:ietf:params:xml:ns:xmpp-stanzas">Your account is not allowed to use this gateway.</text>
            </error>
           </iq>
            """,
            use_values=False
        )
        self.recv(
            """
            <iq from='eve@nothingshakespearian' type='set' to='aim.shakespeare.lit' id='1'>
              <query xmlns='jabber:iq:register'>
                <username>bill</username>
                <password>Calliope</password>
               </query>
            </iq>
            """
        )
        self.send(
            """
           <iq xmlns="jabber:component:accept" from="aim.shakespeare.lit" type="error" to="eve@nothingshakespearian" id="1">
            <error type="cancel">
                <not-allowed xmlns="urn:ietf:params:xml:ns:xmpp-stanzas"/>
                <text xmlns="urn:ietf:params:xml:ns:xmpp-stanzas">Your account is not allowed to use this gateway.</text>
            </error>
           </iq>
            """,
            use_values=False
        )

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
        juliet = self.xmpp.loop.run_until_complete(
            session.contacts.by_jid(JID("juliet@aim.shakespeare.lit"))
        )
        juliet.react("legacy1", "👋")
        msg = self.next_sent()
        assert msg["reactions"]["id"] == "legacy1"
        for r in msg["reactions"]:
            assert r["value"] == "👋"

    def test_last_seen(self):
        session = BaseSession.get_self_or_unique_subclass().from_jid(
            JID("romeo@montague.lit")
        )
        juliet = self.xmpp.loop.run_until_complete(
            session.contacts.by_jid(JID("juliet@aim.shakespeare.lit"))
        )
        now = datetime.datetime.now(datetime.timezone.utc)
        juliet.away(last_seen=now)
        sent = self.next_sent()
        assert sent["idle"]["since"] == now

    def test_disco_adhoc_commands_unregistered(self):
        self.recv(
            f"""
            <iq type='get'
                from='requester@domain'
                to='{self.xmpp.boundjid.bare}'>
              <query xmlns='http://jabber.org/protocol/disco#items'
                     node='http://jabber.org/protocol/commands'/>
            </iq>
            """
        )
        self.send(
            f"""
            <iq type='result'
                to='requester@domain'
                from='{self.xmpp.boundjid.bare}' id='1'>
              <query xmlns='http://jabber.org/protocol/disco#items'
                     node='http://jabber.org/protocol/commands'>
                <item jid="aim.shakespeare.lit" node="jabber:iq:register" name="Register to the gateway"/>
              </query>
            </iq>
            """
        )

    def test_disco_adhoc_commands_as_logged_user(self):
        self.recv(
            f"""
            <iq type='get'
                from='romeo@montague.lit/gajim'
                to='{self.xmpp.boundjid.bare}'>
              <query xmlns='http://jabber.org/protocol/disco#items'
                     node='http://jabber.org/protocol/commands'/>
            </iq>
            """
        )
        self.send(
            f"""
            <iq type='result'
                to='romeo@montague.lit/gajim'
                from='{self.xmpp.boundjid.bare}' id='1'>
              <query xmlns='http://jabber.org/protocol/disco#items'
                     node='http://jabber.org/protocol/commands'>
                <item jid="aim.shakespeare.lit" node="search" name="Search for contacts" />
                <item jid="aim.shakespeare.lit" node="unregister" name="Unregister to the gateway"/>
                <item jid="aim.shakespeare.lit" node="sync-contacts" name="Sync XMPP roster"/>
              </query>
            </iq>
            """
        )

    def test_disco_adhoc_commands_as_non_logged_user(self):
        self.get_romeo_session().logged = False
        self.recv(
            f"""
            <iq type='get'
                from='romeo@montague.lit/gajim'
                to='{self.xmpp.boundjid.bare}'>
              <query xmlns='http://jabber.org/protocol/disco#items'
                     node='http://jabber.org/protocol/commands'/>
            </iq>
            """
        )
        self.send(
            f"""
            <iq type='result'
                to='romeo@montague.lit/gajim'
                from='{self.xmpp.boundjid.bare}' id='1'>
              <query xmlns='http://jabber.org/protocol/disco#items'
                     node='http://jabber.org/protocol/commands'>
                <item jid="aim.shakespeare.lit" node="unregister" name="Unregister to the gateway"/>
                <item jid="aim.shakespeare.lit" node="re-login" name="Re-login to the legacy network"/>
              </query>
            </iq>
            """
        )
        self.get_romeo_session().logged = True

    def test_disco_adhoc_commands_as_admin(self):
        # monkeypatch.setattr(config, "ADMINS", ("romeo@montague.lit",))
        config.ADMINS = (JID("admin@montague.lit"),)
        self.recv(
            f"""
            <iq type='get'
                from='admin@montague.lit/gajim'
                to='{self.xmpp.boundjid.bare}'>
              <query xmlns='http://jabber.org/protocol/disco#items'
                     node='http://jabber.org/protocol/commands'/>
            </iq>
            """
        )
        self.send(
            f"""
            <iq type='result'
                to='admin@montague.lit/gajim'
                from='{self.xmpp.boundjid.bare}' id='1'>
              <query xmlns='http://jabber.org/protocol/disco#items'
                     node='http://jabber.org/protocol/commands'>
                <item jid="aim.shakespeare.lit" node="info" name="List registered users" />
                <item jid="aim.shakespeare.lit" node="delete_user" name="Delete a user" />
                <item jid="aim.shakespeare.lit" node="jabber:iq:register" name="Register to the gateway"/>
              </query>
            </iq>
            """
        )
        config.ADMINS = ()

    def test_adhoc_forbidden_non_admin(self):
        self.recv(
            f"""
            <iq type="set" from="test@localhost/gajim" to="{self.xmpp.boundjid.bare}" id="123">
                <command xmlns="http://jabber.org/protocol/commands" action="execute" node="delete_user" />
            </iq>
            """
        )
        self.send(
            f"""
            <iq xmlns="jabber:component:accept" type="error" from="aim.shakespeare.lit" to="test@localhost/gajim" id="123">
              <error type="auth">
                <not-authorized xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
            </error>
            </iq>
            """,
            use_values=False,
        )

    def test_disco_component(self):
        self.recv(
            f"""
            <iq type="get" from="test@localhost/gajim" to="{self.xmpp.boundjid.bare}" id="123">
                <query xmlns='http://jabber.org/protocol/disco#info'/>
            </iq>
            """
        )
        self.send(
            f"""
            <iq xmlns="jabber:component:accept" type="result" from="aim.shakespeare.lit" to="test@localhost/gajim" id="123">
              <query xmlns="http://jabber.org/protocol/disco#info">
                <identity category="conference" type="text" name="Slidged rooms" />
                <identity category="account" type="registered" name="SLIDGE TEST" />
                <identity category="pubsub" type="pep" name="SLIDGE TEST" />
                <identity category="gateway" type="" name="SLIDGE TEST" />
                <feature var="jabber:iq:search" />
                <feature var="jabber:iq:register" />
                <feature var="jabber:iq:gateway" />
                <feature var="urn:ietf:params:xml:ns:vcard-4.0" />
                <feature var="http://jabber.org/protocol/pubsub#event" />
                <feature var="http://jabber.org/protocol/pubsub#retrieve-items" />
                <feature var="http://jabber.org/protocol/pubsub#persistent-items" />
                <feature var="http://jabber.org/protocol/muc" />
                <feature var="http://jabber.org/protocol/commands" />
                <feature var="urn:xmpp:mam:2"/>
           		<feature var="urn:xmpp:mam:2#extended"/>
              </query>
            </iq>
            """
        )

    def test_disco_local_part_unregistered(self):
        self.recv(
            f"""
            <iq type="get" from="test@localhost/gajim" to="juliet@{self.xmpp.boundjid.bare}" id="123">
                <query xmlns='http://jabber.org/protocol/disco#info'/>
            </iq>
            """
        )
        self.send(
            f"""
            <iq xmlns="jabber:component:accept" type="error" from="juliet@aim.shakespeare.lit" to="test@localhost/gajim" id="123">
              <error type="auth">
                <registration-required xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
              </error>
            </iq>
            """,
            use_values=False,
        )

    def test_disco_registered_existing_contact(self):
        self.recv(
            f"""
            <iq type="get" from="romeo@montague.lit/gajim" to="juliet@{self.xmpp.boundjid.bare}/slidge" id="123">
                <query xmlns='http://jabber.org/protocol/disco#info'/>
            </iq>
            """
        )
        self.send(
            f"""
            <iq xmlns="jabber:component:accept" type="result"
                from="juliet@aim.shakespeare.lit/slidge" to="romeo@montague.lit/gajim" id="123">
              <query xmlns="http://jabber.org/protocol/disco#info">
              <identity category="client" type="pc" />
                <feature var="http://jabber.org/protocol/chatstates" />
                <feature var="urn:xmpp:receipts" />
                <feature var="urn:xmpp:message-correct:0" />
                <feature var="urn:xmpp:chat-markers:0" />
                <feature var="jabber:x:oob" />
                <feature var="urn:xmpp:reactions:0" />
                <feature var="urn:xmpp:message-retract:0" />
                <feature var="urn:xmpp:reply:0" />
                <feature var="urn:ietf:params:xml:ns:vcard-4.0" />
              </query>
            </iq>
            """,
        )

    def test_non_existing_contact(self):
        self.recv(
            f"""
            <message from="romeo@montague.lit/gajim" to="nope@{self.xmpp.boundjid.bare}/slidge" id="123">
              <body>DSAD</body>
            </message>
            """
        )
        self.send(
            f"""
            <message xmlns="jabber:component:accept" type="error" from="nope@aim.shakespeare.lit/slidge" to="romeo@montague.lit/gajim" id="123">
              <error type="cancel">
                <item-not-found xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
                <text xmlns="urn:ietf:params:xml:ns:xmpp-stanzas">Only juliet</text>
              </error>
            </message>
            """,
            use_values=False,
        )


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
        juliet = self.xmpp.loop.run_until_complete(
            session.contacts.by_jid(JID("juliet@aim.shakespeare.lit"))
        )
        juliet.send_text("body", carbon=True)
        self.send(
            """
            <message to="shakespeare.lit" from="aim.shakespeare.lit">
              <privilege xmlns="urn:xmpp:privilege:1">
                <forwarded xmlns="urn:xmpp:forward:0">
                  <message xmlns="jabber:client" to="juliet@aim.shakespeare.lit" type="chat" from="romeo@shakespeare.lit">
                    <body>body</body>
                    <store xmlns="urn:xmpp:hints" />
                    <active xmlns="http://jabber.org/protocol/chatstates"/>
                    <markable xmlns="urn:xmpp:chat-markers:0"/>
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

    def test_privilege(self):
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
        juliet = self.xmpp.loop.run_until_complete(
            session.contacts.by_jid(JID("juliet@aim.shakespeare.lit"))
        )
        juliet.send_text("body", carbon=True)
        self.send(
            """
            <message to="shakespeare.lit" from="aim.shakespeare.lit">
              <privilege xmlns="urn:xmpp:privilege:2">
                <forwarded xmlns="urn:xmpp:forward:0">
                  <message xmlns="jabber:client" to="juliet@aim.shakespeare.lit" type="chat" from="romeo@shakespeare.lit">
                    <body>body</body>
                    <store xmlns="urn:xmpp:hints" />
                    <markable xmlns="urn:xmpp:chat-markers:0"/>
                    <active xmlns="http://jabber.org/protocol/chatstates"/>
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


class TestContact(SlidgeTest):
    plugin = globals()

    def setUp(self):
        super().setUp()
        user_store.add(
            JID("romeo@montague.lit/gajim"), {"username": "romeo", "city": ""}
        )
        self.get_romeo_session().logged = True

    @staticmethod
    def get_romeo_session() -> Session:
        return BaseSession.get_self_or_unique_subclass().from_jid(
            JID("romeo@montague.lit")
        )

    def get_juliet(self) -> LegacyContact:
        session = self.get_romeo_session()
        return self.xmpp.loop.run_until_complete(session.contacts.by_legacy_id(123))

    def test_caps(self):
        juliet = self.get_juliet()
        juliet.online()
        self.send(
            """
            <presence xmlns="jabber:component:accept" from="juliet@aim.shakespeare.lit/slidge" to="romeo@montague.lit">
                <c xmlns="http://jabber.org/protocol/caps"
                   node="http://slixmpp.com/ver/1.8.3"
                   hash="sha-1"
                   ver="nX+H2K5ZqWS5nDTwmCHz6bln5KQ="/>
                <priority>0</priority>
            </presence>
            """
        )


class TestCarbon(SlidgeTest):
    plugin = globals()

    def setUp(self):
        super().setUp()
        user_store.add(
            JID("romeo@shakespeare.lit/gajim"), {"username": "romeo", "city": ""}
        )
        self.get_romeo_session().logged = True

    @staticmethod
    def get_romeo_session() -> Session:
        return BaseSession.get_self_or_unique_subclass().from_jid(
            JID("romeo@shakespeare.lit")
        )

    def get_juliet(self) -> LegacyContact:
        session = self.get_romeo_session()
        return self.xmpp.loop.run_until_complete(session.contacts.by_legacy_id(123))

    def test_carbon_send(self):
        orig = AttachmentMixin._AttachmentMixin__get_url

        async def get_url(self, file_path, *a, **k):
            return False, file_path, "URL"

        AttachmentMixin._AttachmentMixin__get_url = get_url

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

        juliet = self.get_juliet()
        juliet.send_text("TEXT", carbon=True)
        self.send(
            """
   <message xmlns="jabber:component:accept" to="shakespeare.lit" from="aim.shakespeare.lit" type="normal">
   	<privilege xmlns="urn:xmpp:privilege:2">
   		<forwarded xmlns="urn:xmpp:forward:0">
   			<message xmlns="jabber:client" type="chat" from="romeo@shakespeare.lit" to="juliet@aim.shakespeare.lit">
   				<body>TEXT</body>
   				<active xmlns="http://jabber.org/protocol/chatstates"/>
   				<store xmlns="urn:xmpp:hints"/>
   				<markable xmlns="urn:xmpp:chat-markers:0"/>
   			</message>
   		</forwarded>
   	</privilege>
   </message>
            """
        )
        with tempfile.NamedTemporaryFile("w+") as f:
            f.write("test")
            f.seek(0)
            self.xmpp.loop.run_until_complete(
                juliet.send_file(file_path=f.name, carbon=True)
            )
            stamp = format_datetime(
                datetime.datetime.fromtimestamp(Path(f.name).stat().st_mtime)
            )
            self.send(
                f"""
       <message xmlns="jabber:component:accept" to="shakespeare.lit" from="aim.shakespeare.lit" type="normal">
        <privilege xmlns="urn:xmpp:privilege:2">
            <forwarded xmlns="urn:xmpp:forward:0">
                <message xmlns="jabber:client" type="chat" from="romeo@shakespeare.lit" to="juliet@aim.shakespeare.lit">
                    <reference xmlns="urn:xmpp:reference:0" type="data">
                        <media-sharing xmlns="urn:xmpp:sims:1">
                            <sources>
                                <reference xmlns="urn:xmpp:reference:0" uri="URL" type="data"/>
                            </sources>
                            <file xmlns="urn:xmpp:jingle:apps:file-transfer:5">
                                <name>{Path(f.name).name}</name>
                                <size>4</size>
                                <date>{stamp}</date>
                                <hash xmlns="urn:xmpp:hashes:2" algo="sha-256">n4bQgYhMfWWaL+qgxVrQFaO/TxsrC4Is0V1sFbDwCgg=</hash>
                            </file>
                        </media-sharing>
                    </reference>
                    <file-sharing xmlns="urn:xmpp:sfs:0" disposition="inline">
                        <sources>
                            <url-data xmlns="http://jabber.org/protocol/url-data" target="URL"/>
                        </sources>
                        <file xmlns="urn:xmpp:file:metadata:0">
                            <name>{Path(f.name).name}</name>
                            <size>4</size>
                            <date>{stamp}</date>
                            <hash xmlns="urn:xmpp:hashes:2" algo="sha-256">n4bQgYhMfWWaL+qgxVrQFaO/TxsrC4Is0V1sFbDwCgg=</hash>
                        </file>
                    </file-sharing>
                    <x xmlns="jabber:x:oob">
                        <url>URL</url>
                    </x>
                    <body>URL</body>
                </message>
            </forwarded>
        </privilege>
       </message>
                """
            )
        AttachmentMixin._AttachmentMixin__get_url = orig


log = logging.getLogger(__name__)
