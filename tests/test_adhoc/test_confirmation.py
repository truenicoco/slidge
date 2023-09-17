import pytest
import slixmpp.test
from slixmpp import ComponentXMPP
from slixmpp.plugins.xep_0050.adhoc import XEP_0050

import slidge.command.adhoc
from slidge.command import Command, Confirmation
from slidge.command.adhoc import AdhocProvider
from slidge.util.test import SlixTestPlus


class MockSession:
    def __init__(self, jid):
        self.jid = jid
        self.logged = "logged" in jid.username


@pytest.fixture(autouse=True)
def mock(monkeypatch, MockRE):
    monkeypatch.setattr(
        slidge.command.base, "is_admin", lambda j: j.username.startswith("admin")
    )
    monkeypatch.setattr(Command, "_get_session", lambda s, j: MockSession(j))
    monkeypatch.setattr(
        slixmpp.test.ComponentXMPP,
        "get_session_from_stanza",
        lambda self, stanza: MockSession(stanza.get_from()),
        raising=False,
    )
    monkeypatch.setattr(XEP_0050, "new_session", lambda _: "session-id")
    monkeypatch.setattr(
        ComponentXMPP,
        "jid_validator",
        MockRE,
        raising=False,
    )


class CommandAdmin(Command):
    NAME = "Command number one"
    NODE = "command1"

    async def run(self, _session, _ifrom):
        return Confirmation(
            prompt="Confirm?", handler=self.finish, success="It worked!"
        )

    async def finish(self, _session, _ifrom):
        pass


class CommandAdminConfirmFail(CommandAdmin):
    NAME = "Command number two"
    NODE = "command2"

    async def run_admin(self):
        return Confirmation(
            prompt="Confirm?", handler=self.finish, success="It worked!"
        )

    async def finish(self, _session, _ifrom):
        raise RuntimeError("Ploup")


class TestCommandsConfirmation(SlixTestPlus):
    def setUp(self):
        super().setUp()
        self.stream_start(
            mode="component",
            plugins=["xep_0050"],
            jid="slidge.whatever.ass",
            server="whatever.ass",
        )
        self.adhoc = AdhocProvider(self.xmpp)
        self.adhoc.register(CommandAdmin(self.xmpp))
        self.adhoc.register(CommandAdminConfirmFail(self.xmpp))

    def test_confirmation_cancel(self):
        self.recv(  # language=XML
            f"""
            <iq type='set'
                from='admin@whatever.ass/cheogram'
                to='{self.xmpp.boundjid.bare}'
                id="1">
              <command xmlns='http://jabber.org/protocol/commands'
                       node='command1'
                       action='execute' />
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
              <command xmlns="http://jabber.org/protocol/commands"
                       node="command1"
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
        self.recv(  # language=XML
            f"""
            <iq type='set'
                from='admin@whatever.ass/cheogram'
                to='{self.xmpp.boundjid.bare}'
                id="2">
              <command xmlns='http://jabber.org/protocol/commands'
                       node='command1'
                       sessionid="session-id"
                       action='cancel' />
            </iq>
            """
        )
        self.recv(  # language=XML
            """
            <iq xmlns="jabber:component:accept"
                type="result"
                from="slidge.whatever.ass"
                to="admin@whatever.ass/cheogram"
                id="2">
              <command xmlns="http://jabber.org/protocol/commands"
                       node="command1"
                       sessionid="session-id"
                       status="canceled" />
            </iq>
            """
        )

    def test_confirmation_do_it(self):
        self.recv(  # language=XML
            f"""
            <iq type='set'
                from='admin@whatever.ass/cheogram'
                to='{self.xmpp.boundjid.bare}'
                id="1">
              <command xmlns='http://jabber.org/protocol/commands'
                       node='command1'
                       action='execute' />
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
              <command xmlns="http://jabber.org/protocol/commands"
                       node="command1"
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
        self.recv(  # language=XML
            f"""
            <iq type='set'
                from='admin@whatever.ass/cheogram'
                to='{self.xmpp.boundjid.bare}'
                id="2">
              <command xmlns='http://jabber.org/protocol/commands'
                       node='command1'
                       sessionid="session-id"
                       action='next'>
                <x xmlns="jabber:x:data"
                   type="submit">
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
        self.send(  # language=XML
            """
            <iq xmlns="jabber:component:accept"
                type="result"
                from="slidge.whatever.ass"
                to="admin@whatever.ass/cheogram"
                id="2">
              <command xmlns="http://jabber.org/protocol/commands"
                       node="command1"
                       sessionid="session-id"
                       status="completed">
                <note type="info">It worked!</note>
              </command>
            </iq>
            """
        )

    def test_confirmation_fail(self):
        self.recv(  # language=XML
            f"""
            <iq type='set'
                from='admin@whatever.ass/cheogram'
                to='{self.xmpp.boundjid.bare}'
                id="1">
              <command xmlns='http://jabber.org/protocol/commands'
                       node='command2'
                       action='execute' />
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
              <command xmlns="http://jabber.org/protocol/commands"
                       node="command2"
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
        self.recv(  # language=XML
            f"""
            <iq type='set'
                from='admin@whatever.ass/cheogram'
                to='{self.xmpp.boundjid.bare}'
                id="2">
              <command xmlns='http://jabber.org/protocol/commands'
                       node='command2'
                       sessionid="session-id"
                       action='complete'>
                <x xmlns="jabber:x:data"
                   type="submit">
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
        self.send(  # language=XML
            """
            <iq xmlns="jabber:component:accept"
                type="error"
                from="slidge.whatever.ass"
                to="admin@whatever.ass/cheogram"
                id="2">
              <error type="wait">
                <internal-server-error xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
                <text xmlns="urn:ietf:params:xml:ns:xmpp-stanzas">Ploup</text>
              </error>
            </iq>
            """,
            use_values=False,
        )
