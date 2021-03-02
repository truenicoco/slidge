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
from slidge.legacy.dummy import MockLegacyClient


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
