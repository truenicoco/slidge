import logging
import tempfile
from pathlib import Path
from typing import Hashable, Optional

import pytest
from slixmpp import JID, Presence
from slixmpp.exceptions import XMPPError
from slixmpp.test import SlixTest, TestTransport

from slidge import *
from slidge.gateway import SLIXMPP_PLUGINS


class SlidgeTest(SlixTest):
    def stream_start(
        self,
        mode="client",
        skip=True,
        header=None,
        socket="mock",
        jid="tester@localhost/resource",
        password="test",
        server="localhost",
        port=5222,
        sasl_mech=None,
        plugins=None,
        plugin_config={},
    ):
        """
        Initialize an XMPP client or component using a dummy XML stream.

        Arguments:
            mode     -- Either 'client' or 'component'. Defaults to 'client'.
            skip     -- Indicates if the first item in the sent queue (the
                        stream header) should be removed. Tests that wish
                        to test initializing the stream should set this to
                        False. Otherwise, the default of True should be used.
            socket   -- Either 'mock' or 'live' to indicate if the socket
                        should be a dummy, mock socket or a live, functioning
                        socket. Defaults to 'mock'.
            jid      -- The JID to use for the connection.
                        Defaults to 'tester@localhost/resource'.
            password -- The password to use for the connection.
                        Defaults to 'test'.
            server   -- The name of the XMPP server. Defaults to 'localhost'.
            port     -- The port to use when connecting to the server.
                        Defaults to 5222.
            plugins  -- List of plugins to register. By default, all plugins
                        are loaded.
        """
        if not plugin_config:
            plugin_config = {}

        class GatewayTest(BaseGateway):
            unregistered = []

            def __init__(self, jid, password, server, port, plugin_config):
                class C:
                    pass

                C.jid = jid
                C.secret = password
                C.server = server
                C.port = port
                C.upload_service = "upload.test"
                C.home_dir = Path(tempfile.mkdtemp())
                C.user_jid_validator = ".*@shakespeare.lit"
                super().__init__(C)

            def unregister(self, user: GatewayUser):
                self.unregistered.append(user)

        self.GatewayTest = GatewayTest

        self.xmpp = GatewayTest(
            jid, password, server, port, plugin_config=plugin_config
        )
        self.xmpp._always_send_everything = True

        self.xmpp.connection_made(TestTransport(self.xmpp))
        self.xmpp.session_bind_event.set()
        # Remove unique ID prefix to make it easier to test
        self.xmpp._id_prefix = ""
        self.xmpp.default_lang = None
        self.xmpp.peer_default_lang = None

        def new_id():
            self.xmpp._id += 1
            return str(self.xmpp._id)

        self.xmpp._id = 0
        self.xmpp.new_id = new_id

        # Must have the stream header ready for xmpp.process() to work.
        if not header:
            header = self.xmpp.stream_header

        self.xmpp.data_received(header)
        self.wait_for_send_queue()

        if skip:
            self.xmpp.socket.next_sent()
            if mode == "component":
                self.xmpp.socket.next_sent()

        if plugins is None:
            self.xmpp.register_plugins()
        else:
            for plugin in plugins:
                self.xmpp.register_plugin(plugin)

        # Some plugins require messages to have ID values. Set
        # this to True in tests related to those plugins.
        self.xmpp.use_message_ids = False
        self.xmpp.use_presence_ids = False


received_presences = []
text_received_by_juliet = []
composing_chat_states_received_by_juliet = []

user_store.set_file(Path(tempfile.mkdtemp()) / "test.db")


class TestAimShakespeareBase(SlidgeTest):
    @pytest.fixture(autouse=True, scope="module")
    def roster(self):
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

        yield Roster

        LegacyRoster.reset_subclass()

    @pytest.fixture(autouse=True, scope="module")
    def session(self):
        class Session(BaseSession):
            def __init__(self, user):
                super().__init__(user)

            async def login(self, p: Presence):
                received_presences.append(p)

            async def logout(self, p: Optional[Presence]):
                received_presences.append(p)

            async def send_text(self, t: str, c: LegacyContact) -> Optional[Hashable]:
                text_received_by_juliet.append((t, c))
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
                composing_chat_states_received_by_juliet.append(c)

            async def displayed(self, legacy_msg_id: Hashable, c: LegacyContact):
                pass

        # self.Session = Session
        yield Session
        Session.reset_subclass()

    def setUp(self):
        user_store.add(
            JID("romeo@montague.lit/gajim"), {"username": "romeo", "city": ""}
        )
        BaseGateway.reset_subclass()
        self.stream_start(
            mode="component",
            plugins=SLIXMPP_PLUGINS,
            jid="aim.shakespeare.lit",
            server="shakespeare.lit",
            plugin_config={
                "xep_0100": {"component_name": "AIM Gateway", "type": "aim"}
            },
        )

    @classmethod
    def tearDownClass(cls):
        BaseGateway.reset_subclass()
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
        assert len(text_received_by_juliet) == 1
        text, contact = text_received_by_juliet[-1]
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
        assert len(self.GatewayTest.unregistered) == 0
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
        # self.send(None)
        assert len(self.GatewayTest.unregistered) == 1
        assert self.GatewayTest.unregistered[0].jid == "romeo@montague.lit"

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


log = logging.getLogger(__name__)
