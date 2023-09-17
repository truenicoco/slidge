import pytest
from slixmpp import JID, ComponentXMPP
from slixmpp.plugins.xep_0050.adhoc import XEP_0050
from slixmpp.test import SlixTest

import slidge.command.adhoc
from slidge.command import Command, TableResult
from slidge.command.adhoc import AdhocProvider
from slidge.command.base import FormField


class MockSession:
    def __init__(self, jid):
        self.logged = True


@pytest.fixture(autouse=True)
def mock(monkeypatch, MockRE):
    monkeypatch.setattr(
        slidge.command.base, "is_admin", lambda j: j.username.startswith("admin")
    )
    monkeypatch.setattr(Command, "_get_session", lambda s, j: MockSession(j))
    monkeypatch.setattr(
        ComponentXMPP,
        "jid_validator",
        MockRE,
        raising=False,
    )
    monkeypatch.setattr(XEP_0050, "new_session", lambda _: "session-id")


class Command1(Command):
    NAME = "Command number one"
    NODE = "command1"

    async def run(self, _session, _ifrom):
        return TableResult(
            description="A description",
            fields=[
                FormField("name", label="JID"),
                FormField("jid", type="jid-single", label="JID"),
            ],
            items=[
                {"jid": JID("test@test"), "name": "Some dude"},
                {"jid": "test2@test", "name": "Some dude2"},
            ],
        )


class TestCommandsResults(SlixTest):
    def setUp(self):
        self.stream_start(
            mode="component",
            plugins=["xep_0050"],
            jid="slidge.whatever.ass",
            server="whatever.ass",
        )
        self.adhoc = AdhocProvider(self.xmpp)
        self.adhoc.register(Command1(self.xmpp))

    def test_table_result(self):
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
                       status="completed">
                <x xmlns="jabber:x:data"
                   type="result">
                  <title>A description</title>
                  <reported>
                    <field var="name"
                           type="text-single"
                           label="JID" />
                    <field var="jid"
                           type="jid-single"
                           label="JID" />
                  </reported>
                  <item>
                    <field var="name">
                      <value>Some dude2</value>
                    </field>
                    <field var="jid">
                      <value>test2@test</value>
                    </field>
                  </item>
                  <item>
                    <field var="name">
                      <value>Some dude</value>
                    </field>
                    <field var="jid">
                      <value>test@test</value>
                    </field>
                  </item>
                </x>
              </command>
            </iq>
            """,
            use_values=False,
        )
