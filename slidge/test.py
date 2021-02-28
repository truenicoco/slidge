import asyncio
import logging
from datetime import datetime, timedelta
from configparser import ConfigParser
import xml.dom.minidom
from pathlib import Path

import slixmpp.test
from slixmpp import Message
from slixmpp.xmlstream.tostring import tostring
from slixmpp.test import SlixTest, TestTransport

from slidge.database import User
from slidge.muc import LegacyMuc
from slidge.buddy import Buddy
from slidge.session import sessions
from slidge.gateway import BaseGateway
from slidge.base_legacy import LegacyError, BaseLegacyClient


def my_tostring(xml):
    return (
        xml.dom.minidom.parseString(tostring(xml))
        .toprettyxml()
        .replace('<?xml version="1.0" ?>', "")
    )


# FIXME: monkey patch SlixTest.tostring for pretty printing
# For some reason this doesn't work
slixmpp.test.tostring = my_tostring

log = logging.getLogger(__name__)

assets_path = Path(__file__).parent.parent / "assets"


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


class SlixGatewayTest(SlixTest):
    def next_sent(self):
        self.wait_for_send_queue()
        sent = self.xmpp.socket.next_sent(timeout=0.5)
        if sent is None:
            return None
        xml = self.parse_xml(sent)
        self.fix_namespaces(xml, "jabber:component:accept")
        sent = self.xmpp._build_stanza(xml, "jabber:component:accept")
        return sent

    def stream_start(
        self,
        config=None,
        mode="gateway",
        skip=True,
        header=None,
        socket="mock",
        sasl_mech=None,
        db_echo=False,
        gateway_jid="gateway.example.com",
        server="example.com",
    ):
        """
        Initialize an XMPP client or component using a dummy XML stream.

        Arguments:
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
        if config is None:
            config = ConfigParser()
            config.add_section("component")
            config.add_section("database")
            config.add_section("legacy")
            config.add_section("buddies")
            config.add_section("gateway")
            config["component"]["jid"] = gateway_jid
            config["component"]["secret"] = "test"
            config["component"]["server"] = server
            config["component"]["port"] = "5222"
            config["database"]["path"] = "sqlite://"
            config["database"]["echo"] = str(db_echo)
            config["buddies"]["resource"] = "gateway"
            config["buddies"]["group"] = "legacy"
            config["gateway"]["send-muc-invitations-on-connect"] = "false"
            config["gateway"]["stay-connected"] = "false"

        self.xmpp = BaseGateway(config, client_cls=MockLegacyClient)

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

        # Some plugins require messages to have ID values. Set
        # this to True in tests related to those plugins.
        self.xmpp.use_message_ids = False
        self.xmpp.use_presence_ids = False
