import pytest
from slixmpp import ComponentXMPP

import slidge.core.command.adhoc
import slidge.core.command.base
from slidge.core.command import Command, CommandAccess
from slidge.core.command.adhoc import AdhocProvider
from slidge.util.test import SlixTestPlus
from slidge.util.xep_0050.adhoc import XEP_0050


class MockSession:
    def __init__(self, jid):
        self.logged = "logged" in jid.username


@pytest.fixture(autouse=True)
def mock(monkeypatch, MockRE):
    monkeypatch.setattr(
        slidge.core.command.base, "is_admin", lambda j: j.username.startswith("admin")
    )
    monkeypatch.setattr(Command, "_get_session", lambda s, j: MockSession(j))
    monkeypatch.setattr(XEP_0050, "new_session", lambda _: "session-id")
    monkeypatch.setattr(
        ComponentXMPP,
        "jid_validator",
        MockRE,
        raising=False,
    )


class Command1(Command):
    NAME = "Command number one"
    NODE = "command1"
    ACCESS = CommandAccess.ADMIN_ONLY


class Command2(Command1):
    NAME = "Command number two"
    NODE = "command2"
    ACCESS = CommandAccess.ADMIN_ONLY


class TestCommandsDisco(SlixTestPlus):
    def setUp(self):
        self.stream_start(
            mode="component",
            plugins=["xep_0050"],
            jid="slidge.whatever.ass",
            server="whatever.ass",
        )
        self.adhoc = AdhocProvider(self.xmpp)
        self.adhoc.register(Command1(self.xmpp))
        self.adhoc.register(Command2(self.xmpp))
        super().setUp()

    def test_disco_admin(self):
        self.recv(
            f"""
            <iq type='get'
                from='admin@whatever.ass/cheogram'
                to='{self.xmpp.boundjid.bare}'>
              <query xmlns='http://jabber.org/protocol/disco#items'
                     node='http://jabber.org/protocol/commands'/>
            </iq>
            """
        )
        self.send(
            """
            <iq xmlns="jabber:component:accept" type="result" from="slidge.whatever.ass" to="admin@whatever.ass/cheogram" id="1">
              <query xmlns="http://jabber.org/protocol/disco#items" node="http://jabber.org/protocol/commands">
                <item jid="slidge.whatever.ass" node="command1" name="Command number one" />
                <item jid="slidge.whatever.ass" node="command2" name="Command number two" />
            </query>
            </iq>
            """
        )

        self.recv(
            f"""
            <iq type='get'
                from='user@whatever.ass/cheogram'
                to='{self.xmpp.boundjid.bare}'>
              <query xmlns='http://jabber.org/protocol/disco#items'
                     node='http://jabber.org/protocol/commands'/>
            </iq>
            """
        )
        self.send(
            """
            <iq xmlns="jabber:component:accept" type="result" from="slidge.whatever.ass" to="user@whatever.ass/cheogram" id="2">
              <query xmlns="http://jabber.org/protocol/disco#items" node="http://jabber.org/protocol/commands" />
            </iq>
            """
        )

    def test_non_existing_command(self):
        self.recv(
            f"""
            <iq type='set'
                from='admin@whatever.ass/cheogram'
                to='{self.xmpp.boundjid.bare}'
                id="1">
              <command xmlns='http://jabber.org/protocol/commands'
                       node='non-existing'
                       action='execute'/>
            </iq>
            """
        )
        self.send(
            """
            <iq xmlns="jabber:component:accept"
                type="error"
                from="slidge.whatever.ass"
                to="admin@whatever.ass/cheogram"
                id="1">
              <error type="cancel">
                <item-not-found xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
              </error>
            </iq>
            """,
            use_values=False,
        )
