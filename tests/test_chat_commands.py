import pytest

import slixmpp.test

import slidge.core.command.chat_command
from slidge.util.test import SlixTestPlus
from slidge.core.command import Command, Confirmation


class MockSession:
    def __init__(self, jid):
        self.logged = "logged" in jid.username


@pytest.fixture(autouse=True)
def mock(monkeypatch, MockRE):
    monkeypatch.setattr(Command, "_get_session", lambda s, j: MockSession(j))
    monkeypatch.setattr(
        slixmpp.test.ComponentXMPP,
        "get_session_from_stanza",
        lambda self, stanza: MockSession(stanza.get_from()),
        raising=False,
    )
    monkeypatch.setattr(
        slixmpp.test.ComponentXMPP,
        "jid_validator",
        MockRE,
        raising=False,
    )


class CommandAdmin(Command):
    NAME = "Command number one"
    CHAT_COMMAND = "command1"

    async def run(self, _session, _ifrom):
        return Confirmation(
            prompt="Confirm?", handler=self.finish, success="It worked!"
        )

    async def finish(self, _session, _ifrom):
        pass


class CommandAdminConfirmFail(CommandAdmin):
    NAME = "Command number two"
    CHAT_COMMAND = "command2"

    async def run_admin(self):
        return Confirmation(
            prompt="Confirm?", handler=self.finish, success="It worked!"
        )

    async def finish(self, _session, _ifrom):
        raise RuntimeError("Ploup")


class TestChatCommands(SlixTestPlus):
    def setUp(self):
        self.stream_start(
            mode="component",
            plugins=["xep_0050"],
            jid="slidge.whatever.ass",
            server="whatever.ass",
        )
        self.commands = slidge.core.command.chat_command.ChatCommandProvider(self.xmpp)
        self.commands.register(CommandAdmin(self.xmpp))
        self.commands.register(CommandAdminConfirmFail(self.xmpp))
        super().setUp()

    def test_non_existing(self):
        self.recv(
            f"""
            <message from='admin@whatever.ass/cheogram'
                     to='{self.xmpp.boundjid.bare}'
                     type='chat'
                     id='not-found'>
              <body>non-existing</body>
            </message>
            """
        )
        t = self.commands.UNKNOWN.format("non-existing")
        self.send(
            f"""
            <message xmlns="jabber:component:accept"
              from="slidge.whatever.ass"
              to="admin@whatever.ass/cheogram"
              type="chat">     
                <body>{t}</body>
            </message>
            """
        )
        self.send(
            f"""
            <message xmlns="jabber:component:accept"
              from="slidge.whatever.ass"
              to="admin@whatever.ass/cheogram"
              type="error"
              id='not-found'>     
              <error type="cancel">
                <item-not-found xmlns="urn:ietf:params:xml:ns:xmpp-stanzas"/>
                <text xmlns="urn:ietf:params:xml:ns:xmpp-stanzas">{t}</text>
              </error>
            </message>
            """,
            use_values=False,
        )

    def test_other_destination(self):
        self.recv(
            f"""
            <message from='admin@whatever.ass/cheogram'
                     to='something@{self.xmpp.boundjid.bare}'
                     type='chat'
                     id='not-found'>
              <body>help</body>
            </message>
            """
        )
        assert self.next_sent() is None

    def test_command_help(self):
        self.recv(
            f"""
            <message from='admin@whatever.ass/cheogram'
                     to='{self.xmpp.boundjid.bare}'
                     type='chat'
                     id='help'>
              <body>help</body>
            </message>
            """
        )
        self.send(
            f"""
            <message xmlns="jabber:component:accept"
              from="slidge.whatever.ass"
              to="admin@whatever.ass/cheogram"
              type="chat">     
              <body>Available commands:\ncommand1 -- Command number one\ncommand2 -- Command number two</body>
            </message>
            """
        )
