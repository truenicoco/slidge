import pytest
from slixmpp import ComponentXMPP
from slixmpp.plugins.xep_0050.adhoc import XEP_0050

import slidge.command.adhoc
import slidge.command.base
from slidge.command import Command, CommandAccess
from slidge.command.adhoc import AdhocProvider
from slidge.command.base import Confirmation
from slidge.util.test import SlixTestPlus


class MockSession:
    def __init__(self, jid):
        self.logged = "logged" in jid.username


@pytest.fixture(autouse=True)
def mock(monkeypatch, MockRE):
    monkeypatch.setattr(
        slidge.command.base, "is_admin", lambda j: j.username.startswith("admin")
    )
    monkeypatch.setattr(Command, "_get_session", lambda s, j: MockSession(j))
    monkeypatch.setattr(XEP_0050, "new_session", lambda _: "session-id")
    monkeypatch.setattr(
        ComponentXMPP,
        "jid_validator",
        MockRE,
        raising=False,
    )
    monkeypatch.setattr(
        ComponentXMPP,
        "get_session_from_stanza",
        lambda s, j: MockSession(j.get_from()),
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


class Command3(Command):
    NAME = "Command number three"
    NODE = "command3"

    CATEGORY = "category"
    ACCESS = CommandAccess.ADMIN_ONLY

    async def run(self, _session, _ifrom):
        return Confirmation(
            prompt="Confirm?", handler=self.finish, success="It worked!"
        )

    async def finish(self, _session, _ifrom):
        pass


class Command4(Command):
    NAME = "Command number four"
    NODE = "command4"

    CATEGORY = "category"
    ACCESS = CommandAccess.ADMIN_ONLY

    async def run(self, _session, _ifrom):
        return Confirmation(
            prompt="Confirm?", handler=self.finish, success="It worked!"
        )

    async def finish(self, _session, _ifrom):
        return "OK"


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
        self.adhoc.register(Command3(self.xmpp))
        self.adhoc.register(Command4(self.xmpp))
        super().setUp()

    def test_disco_admin(self):
        self.recv(  # language=XML
            f"""
            <iq type='get'
                from='admin@whatever.ass/cheogram'
                to='{self.xmpp.boundjid.bare}'>
              <query xmlns='http://jabber.org/protocol/disco#items'
                     node='http://jabber.org/protocol/commands' />
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq xmlns="jabber:component:accept"
                type="result"
                from="slidge.whatever.ass"
                to="admin@whatever.ass/cheogram"
                id="1">
              <query xmlns="http://jabber.org/protocol/disco#items"
                     node="http://jabber.org/protocol/commands">
                <item jid="slidge.whatever.ass"
                      node="command1"
                      name="Command number one" />
                <item jid="slidge.whatever.ass"
                      node="command2"
                      name="Command number two" />
                <item jid="slidge.whatever.ass"
                      node="category"
                      name="category" />
              </query>
            </iq>
            """
        )

        self.recv(  # language=XML
            f"""
            <iq type='get'
                from='user@whatever.ass/cheogram'
                to='{self.xmpp.boundjid.bare}'>
              <query xmlns='http://jabber.org/protocol/disco#items'
                     node='http://jabber.org/protocol/commands' />
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq xmlns="jabber:component:accept"
                type="result"
                from="slidge.whatever.ass"
                to="user@whatever.ass/cheogram"
                id="2">
              <query xmlns="http://jabber.org/protocol/disco#items"
                     node="http://jabber.org/protocol/commands" />
            </iq>
            """
        )

    def test_non_existing_command(self):
        self.recv(  # language=XML
            f"""
            <iq type='set'
                from='admin@whatever.ass/cheogram'
                to='{self.xmpp.boundjid.bare}'
                id="1">
              <command xmlns='http://jabber.org/protocol/commands'
                       node='non-existing'
                       action='execute' />
            </iq>
            """
        )
        self.send(  # language=XML
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

    def test_category(self):
        self.recv(  # language=XML
            f"""
            <iq type='set'
                from='admin@whatever.ass/cheogram'
                to='{self.xmpp.boundjid.bare}'
                id="1">
              <command xmlns='http://jabber.org/protocol/commands'
                       node='category'
                       action='execute' />
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq type="result"
                from="slidge.whatever.ass"
                to="admin@whatever.ass/cheogram"
                id="1">
              <command xmlns="http://jabber.org/protocol/commands"
                       node="category"
                       sessionid="session-id"
                       status="executing">
                <actions>
                  <next />
                </actions>
                <x xmlns="jabber:x:data"
                   type="form">
                  <title>category</title>
                  <field var="command"
                         type="list-single"
                         label="Command">
                    <option label="Command number three">
                      <value>command3</value>
                    </option>
                    <option label="Command number four">
                      <value>command4</value>
                    </option>
                    <value />
                  </field>
                </x>
              </command>
            </iq>
            """,
            use_values=False,
        )
        self.recv(  # language=XML
            f"""
            <iq type='set'
                from='admin@whatever.ass/cheogram'
                to='{self.xmpp.boundjid.bare}'
                id="2">
              <command xmlns='http://jabber.org/protocol/commands'
                       node='category'
                       sessionid="session-id">
                <x xmlns='jabber:x:data'
                   type='submit'>
                  <field var='command'>
                    <value>command3</value>
                  </field>
                </x>
              </command>
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq type="result"
                from="slidge.whatever.ass"
                to="admin@whatever.ass/cheogram"
                id="2">
              <command xmlns="http://jabber.org/protocol/commands"
                       node="category"
                       sessionid="session-id"
                       status="executing">
                <actions>
                  <next />
                </actions>
                <x xmlns="jabber:x:data"
                   type="form">
                  <title>Confirm?</title>
                  <field var="confirm"
                         label="Confirm"
                         type="boolean">
                    <value>1</value>
                  </field>
                </x>
              </command>
            </iq>
            """
        )
