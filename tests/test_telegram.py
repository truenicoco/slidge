from argparse import Namespace

import pytest
from slixmpp import JID
from aiotdlib import api as tgapi

import slidge.plugins.telegram as plugin
import slidge.plugins.telegram.client
import slidge.plugins.telegram.gateway
from slidge.util.test import SlidgeTest
from slidge import *


class MockTdlib:
    instances = []

    class log:
        def debug(*_):
            pass

    def __init__(self, *_a, **_kw):
        self.instances.append(self)
        self.calls = []

    def __getattr__(self, item):
        async def mock_coroutine(*args, **kwargs):
            self.calls.append([item, locals()])
            print(f"{item} has been called with {args} and {kwargs}")

        return mock_coroutine

    async def is_private_chat(self, chat_id: int):
        return True


class TestTelegramBase(SlidgeTest):
    plugin = plugin

    class Config(SlidgeTest.Config):
        jid = "telegram.test"
        user_jid_validator = ".*"


class TestTelegram(TestTelegramBase):
    def setUp(self):
        slidge.plugins.telegram.session.TelegramClient = MockTdlib
        slidge.plugins.telegram.gateway.Gateway.args = Namespace(
            tdlib_key="", tdlib_path=""
        )
        super().setUp()
        jid = JID("romeo@telegram.test")
        user_store.add(jid, {"phone": "+123"})
        self.romeo = user_store.get_by_jid(jid)

    def test_transport_displayed_chat_marker(self):
        tg_msg_id = 123456789
        tg_chat_id = 12345

        stanza = f"""
            <message
                from='{self.romeo.jid}'
                id='message-2'
                to='{tg_chat_id}@telegram.test'>
              <displayed xmlns='urn:xmpp:chat-markers:0'
                         id='{tg_msg_id}'/>
            </message>
            """
        self.recv(stanza)

        assert len(MockTdlib.instances) == 1
        tg = MockTdlib.instances[0]
        assert tg.calls[0][0] == "request"

        req = tg.calls[0][1]["args"][0]

        assert isinstance(req, tgapi.ViewMessages)
        assert req.chat_id == tg_chat_id
        assert tg_msg_id in req.message_ids


@pytest.mark.asyncio
async def test_ignore_read_marks_confirmation():
    action = tgapi.UpdateChatReadInbox(
        ID=123,
        chat_id=12345,
        last_read_inbox_message_id=123456789,
        unread_count=0,
    )
    tg = MockTdlib()

    class MockSession:
        sent_read_marks = set()

    class Contact:
        carbons = []

        def carbon_read(self, msg_id):
            self.carbons.append(msg_id)

    class Contacts:
        c = Contact()

        def by_legacy_id(self, _id):
            return self.c

    tg.session = MockSession()
    tg.session.contacts = Contacts()

    await slidge.plugins.telegram.client.TelegramClient.handle_ChatReadInbox(tg, action)
    assert len(tg.session.sent_read_marks) == 0
    assert tg.session.contacts.by_legacy_id(12345).carbons[0] == 123456789

    tg.session.contacts.by_legacy_id(12345).carbons = []
    tg.session.sent_read_marks.add(123456789)
    await slidge.plugins.telegram.client.TelegramClient.handle_ChatReadInbox(tg, action)
    assert len(tg.session.sent_read_marks) == 0
    assert len(tg.session.contacts.by_legacy_id(12345).carbons) == 0
