import pytest

from slixmpp import ComponentXMPP
from slixmpp import JID
from slixmpp.test import SlixTest

import slidge.core.command.adhoc
from slidge.util.test import SlixTestPlus
from slidge.util.xep_0050.adhoc import XEP_0050
from slidge.core.command.adhoc import AdhocProvider
from slidge.core.command import Command, Form
from slidge.core.command.base import FormField


class MockSession:
    def __init__(self, jid):
        # self.jid = jid
        self.logged = True


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

    async def run(self, session, ifrom):
        return Form(
            title="A title",
            instructions="Some instructions",
            fields=[
                FormField(
                    "jid",
                    type="jid-single",
                    label="Enter a JID",
                    value="user@host",
                    required=True,
                ),
                FormField(
                    "option",
                    type="list-single",
                    options=[
                        {"label": "Option 1", "value": "option1"},
                        {"label": "Option 2", "value": "option2"},
                    ],
                ),
            ],
            handler=self.finish,
            handler_kwargs={"arg1": "argument 1"},
        )

    @staticmethod
    async def finish(form_values, _session, ifrom, arg1):
        if form_values["jid"] == "bad@bad":
            raise RuntimeError("IT'S BAD, WE'RE FUCKED")
        assert isinstance(form_values["jid"], JID)
        return f"all good mate, {arg1}"


class TestCommandsResults(SlixTestPlus):
    def setUp(self):
        super().setUp()
        self.stream_start(
            mode="component",
            plugins=["xep_0050"],
            jid="slidge.whatever.ass",
            server="whatever.ass",
        )
        self.adhoc = AdhocProvider(self.xmpp)
        self.adhoc.register(Command1(self.xmpp))

    def test_form_ok(self):
        self.recv(
            f"""
            <iq type='set'
                from='admin@whatever.ass/cheogram'
                to='{self.xmpp.boundjid.bare}'
                id="1">
              <command xmlns='http://jabber.org/protocol/commands'
                       node='command1'
                       action='execute'/>
            </iq>
            """
        )
        self.send(
            f"""
            <iq xmlns="jabber:component:accept"
                type="result"
                from="slidge.whatever.ass"
                to="admin@whatever.ass/cheogram" id="1">
            <command xmlns="http://jabber.org/protocol/commands"
                node="command1"
                sessionid="session-id"
                status="executing">
                <actions>
                    <next />
                </actions>
                <x xmlns="jabber:x:data" type="form">
                    <title>A title</title>
                    <instructions>Some instructions</instructions>
                    <field var="jid" type="jid-single" label="Enter a JID">
                        <value>user@host</value>
                        <required />
                    </field>
                    <field var="option" type="list-single">
                        <value />
                        <option label="Option 1">
                            <value>option1</value>
                        </option>
                        <option label="Option 2">
                            <value>option2</value>
                        </option>
                    </field>
                </x>
            </command>
            </iq>
            """
        )
        self.recv(
            f"""
            <iq type='set'
                from='admin@whatever.ass/cheogram'
                to='{self.xmpp.boundjid.bare}'
                id="2">
              <command xmlns='http://jabber.org/protocol/commands'
                       node='command1'
                       sessionid="session-id">
                <x xmlns='jabber:x:data' type='submit'>
                  <field var='jid'>
                    <value>value@value</value>
                  </field>
                  <field var='option'>
                    <value>option1</value>
                  </field>
            </x>
            </command>
            </iq>
            """
        )
        self.send(
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
                <note type="info">all good mate, argument 1</note>
              </command>
            </iq>
            """
        )

    def test_form_bad_option(self):
        self.recv(
            f"""
            <iq type='set'
                from='admin@whatever.ass/cheogram'
                to='{self.xmpp.boundjid.bare}'
                id="1">
              <command xmlns='http://jabber.org/protocol/commands'
                       node='command1'
                       action='execute'/>
            </iq>
            """
        )
        self.send(
            f"""
            <iq xmlns="jabber:component:accept"
                type="result"
                from="slidge.whatever.ass"
                to="admin@whatever.ass/cheogram" id="1">
            <command xmlns="http://jabber.org/protocol/commands"
                node="command1"
                sessionid="session-id"
                status="executing">
                <actions>
                    <next />
                </actions>
                <x xmlns="jabber:x:data" type="form">
                    <title>A title</title>
                    <instructions>Some instructions</instructions>
                    <field var="jid" type="jid-single" label="Enter a JID">
                        <value>user@host</value>
                        <required />
                    </field>
                    <field var="option" type="list-single">
                        <value />
                        <option label="Option 1">
                            <value>option1</value>
                        </option>
                        <option label="Option 2">
                            <value>option2</value>
                        </option>
                    </field>
                </x>
            </command>
            </iq>
            """
        )
        self.recv(
            f"""
            <iq type='set'
                from='admin@whatever.ass/cheogram'
                to='{self.xmpp.boundjid.bare}'
                id="2">
              <command xmlns='http://jabber.org/protocol/commands'
                       node='command1'
                       sessionid="session-id">
                <x xmlns='jabber:x:data' type='submit'>
                  <field var='jid'>
                    <value>value@value</value>
                  </field>
                  <field var='option'>
                    <value>option3</value>
                  </field>
            </x>
            </command>
            </iq>
            """
        )
        self.send(
            """
            <iq xmlns="jabber:component:accept"
                type="error"
                from="slidge.whatever.ass"
                to="admin@whatever.ass/cheogram"
                id="2">
              <error type="modify">
                <not-acceptable xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
                <text xmlns="urn:ietf:params:xml:ns:xmpp-stanzas">
                  Not a valid option: 'option3'
            </text></error>
            </iq>
            """,
            use_values=False,
        )

    def test_form_exc(self):
        self.recv(
            f"""
            <iq type='set'
                from='admin@whatever.ass/cheogram'
                to='{self.xmpp.boundjid.bare}'
                id="1">
              <command xmlns='http://jabber.org/protocol/commands'
                       node='command1'
                       action='execute'/>
            </iq>
            """
        )
        self.send(
            f"""
            <iq xmlns="jabber:component:accept"
                type="result"
                from="slidge.whatever.ass"
                to="admin@whatever.ass/cheogram" id="1">
            <command xmlns="http://jabber.org/protocol/commands"
                node="command1"
                sessionid="session-id"
                status="executing">
                <actions>
                    <next />
                </actions>
                <x xmlns="jabber:x:data" type="form">
                    <title>A title</title>
                    <instructions>Some instructions</instructions>
                    <field var="jid" type="jid-single" label="Enter a JID">
                        <value>user@host</value>
                        <required />
                    </field>
                    <field var="option" type="list-single">
                        <value />
                        <option label="Option 1">
                            <value>option1</value>
                        </option>
                        <option label="Option 2">
                            <value>option2</value>
                        </option>
                    </field>
                </x>
            </command>
            </iq>
            """
        )
        self.recv(
            f"""
            <iq type='set'
                from='admin@whatever.ass/cheogram'
                to='{self.xmpp.boundjid.bare}'
                id="2">
              <command xmlns='http://jabber.org/protocol/commands'
                       node='command1'
                       sessionid="session-id">
                <x xmlns='jabber:x:data' type='submit'>
                  <field var='jid'>
                    <value>bad@bad</value>
                  </field>
            </x>
            </command>
            </iq>
            """
        )
        self.send(
            """
            <iq xmlns="jabber:component:accept" type="error" from="slidge.whatever.ass"
                to="admin@whatever.ass/cheogram" id="2">
                <error type="wait">
                    <internal-server-error xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
                    <text xmlns="urn:ietf:params:xml:ns:xmpp-stanzas">IT&apos;S BAD, WE&apos;RE FUCKED</text>
                </error>
            </iq>
            """,
            use_values=False,
        )

    def test_form_bad_jid(self):
        self.recv(
            f"""
            <iq type='set'
                from='admin@whatever.ass/cheogram'
                to='{self.xmpp.boundjid.bare}'
                id="1">
              <command xmlns='http://jabber.org/protocol/commands'
                       node='command1'
                       action='execute'/>
            </iq>
            """
        )
        self.send(
            f"""
            <iq xmlns="jabber:component:accept"
                type="result"
                from="slidge.whatever.ass"
                to="admin@whatever.ass/cheogram" id="1">
            <command xmlns="http://jabber.org/protocol/commands"
                node="command1"
                sessionid="session-id"
                status="executing">
                <actions>
                    <next />
                </actions>
                <x xmlns="jabber:x:data" type="form">
                    <title>A title</title>
                    <instructions>Some instructions</instructions>
                    <field var="jid" type="jid-single" label="Enter a JID">
                        <value>user@host</value>
                        <required />
                    </field>
                    <field var="option" type="list-single">
                        <value />
                        <option label="Option 1">
                            <value>option1</value>
                        </option>
                        <option label="Option 2">
                            <value>option2</value>
                        </option>
                    </field>
                </x>
            </command>
            </iq>
            """
        )
        self.recv(
            f"""
            <iq type='set'
                from='admin@whatever.ass/cheogram'
                to='{self.xmpp.boundjid.bare}'
                id="2">
              <command xmlns='http://jabber.org/protocol/commands'
                       node='command1'
                       sessionid="session-id">
                <x xmlns='jabber:x:data' type='submit'>
                  <field var='jid'>
                    <value>bad@bad@bad</value>
                  </field>
            </x>
            </command>
            </iq>
            """
        )
        self.send(
            """
            <iq xmlns="jabber:component:accept" type="error" from="slidge.whatever.ass"
                to="admin@whatever.ass/cheogram" id="2">
                <error type="modify">
                <not-acceptable xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
                <text xmlns="urn:ietf:params:xml:ns:xmpp-stanzas">Not a valid JID: &apos;bad@bad@bad&apos;</text>
            </error>
            </iq>
            """,
            use_values=False,
        )
