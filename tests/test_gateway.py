import logging
import tempfile
from pathlib import Path
from typing import Dict, Hashable, Optional

import pytest

import slixmpp.test.slixtest
from slixmpp import Iq, JID, Presence
from slixmpp.exceptions import XMPPError
from slixmpp.test import SlixTest

from slidge.gateway import SLIXMPP_PLUGINS
from slidge import *


class GatewayTest(BaseGateway):
    def __init__(self, jid, password, server, port, plugin_config):
        class C:
            pass

        C.jid = jid
        C.secret = password
        C.server = server
        C.port = port
        C.upload_service = "upload.test"
        super().__init__(C)


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


class Session(BaseSession):
    received_presences = []
    text_received_by_juliet = []
    composing_chat_states_received_by_juliet = []

    def __init__(self, user):
        super().__init__(user)

    async def login(self, p: Presence):
        self.received_presences.append(p)
        pass

    async def logout(self, p: Presence):
        self.received_presences.append(p)
        pass

    async def send_text(self, t: str, c: LegacyContact) -> Optional[Hashable]:
        self.text_received_by_juliet.append((t, c))
        assert self.user.bare_jid == "romeo@montague.lit"
        assert self.user.jid == JID("romeo@montague.lit")
        if c.jid_username != "juliet":
            raise XMPPError(text="Not found", condition="item-not-found")
        else:
            c.send_text("I love you")
            return 0

    async def send_file(self, u: str, c: LegacyContact) -> Optional[Hashable]:
        pass

    async def active(self, c: LegacyContact):
        pass

    async def inactive(self, c: LegacyContact):
        pass

    async def composing(self, c: LegacyContact):
        self.composing_chat_states_received_by_juliet.append(c)

    async def displayed(self, legacy_msg_id: Hashable, c: LegacyContact):
        pass


slixmpp.test.slixtest.ComponentXMPP = GatewayTest

# FIXME: This test must be run before others because it messes up
#        slidge.BaseSession.__subclasses__ and I did not find
#        a way to avoid this side-effect. Any help about that is welcome.


@pytest.mark.order(1)
class TestAimShakespeareBase(SlixTest):
    def setUp(self):
        self.stream_start(
            mode="component",
            plugins=SLIXMPP_PLUGINS,
            jid="aim.shakespeare.lit",
            server="shakespeare.lit",
            plugin_config={
                "xep_0100": {"component_name": "AIM Gateway", "type": "aim"}
            },
        )
        self.shelf_path = Path(tempfile.mkdtemp()) / "test.db"
        user_store.set_file(self.shelf_path)
        user_store.add(
            JID("romeo@montague.lit/gajim"), {"username": "romeo", "city": ""}
        )

    def tearDown(self):
        user_store._users = None

    def next_sent(self):
        self.wait_for_send_queue()
        sent = self.xmpp.socket.next_sent(timeout=1)
        if sent is None:
            return None
        xml = self.parse_xml(sent)
        self.fix_namespaces(xml, "jabber:component:accept")
        sent = self.xmpp._build_stanza(xml, "jabber:component:accept")
        return sent

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
        assert len(Session.text_received_by_juliet) == 1
        text, contact = Session.text_received_by_juliet[-1]
        assert text == "Art thou not Romeo, and a Montague?"
        assert contact.legacy_id == 123
        m = self.next_sent()
        assert m.get_from() == "juliet@aim.shakespeare.lit/slidge"
        assert m["body"] == "I love you"

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
        assert len(Session.composing_chat_states_received_by_juliet) == 1
        assert Session.composing_chat_states_received_by_juliet[0].legacy_id == 123

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
        session = Session.from_jid(JID("romeo@montague.lit"))
        juliet = session.contacts.by_jid(JID("juliet@aim.shakespeare.lit"))
        msg = juliet.send_text(body="What what?")

        # msg = self.next_sent()
        #  ^ this would be better but works when the test is run alone and fails
        # when all tests are run at once...

        assert msg["from"] == f"juliet@aim.shakespeare.lit/{LegacyContact.RESOURCE}"
        assert msg["to"] == "romeo@montague.lit"
        assert msg["body"] == "What what?"


log = logging.getLogger(__name__)
