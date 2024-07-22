import datetime
import unittest.mock

import sqlalchemy as sa
from slixmpp import Message

from slidge import BaseGateway, BaseSession
from slidge.core.session import _sessions
from slidge.db.models import ArchivedMessage
from slidge.util.archive_msg import HistoryMessage
from slidge.util.test import SlidgeTest


class Gateway(BaseGateway):
    COMPONENT_NAME = "A test"


class Session(BaseSession):
    async def login(self):
        return "YUP"


class TestBackfill(SlidgeTest):
    plugin = globals()
    xmpp: Gateway

    def setUp(self):
        super().setUp()
        self.setup_logged_session()
        self.xmpp.LEGACY_MSG_ID_TYPE = int

    def tearDown(self):
        with self.xmpp.store.session() as orm:
            orm.execute(sa.delete(ArchivedMessage))
        super().tearDown()
        _sessions.clear()

    def test_empty_archive(self):
        with unittest.mock.patch("slidge.group.LegacyMUC.backfill") as backfill:
            self.run_coro(self.room._LegacyMUC__fill_history())
        backfill.assert_awaited_with(None, None)

    def test_live_no_id_before_backfill(self):
        self.first_witch.send_text("BODY 1")
        self.first_witch.send_text("BODY 2")
        self.first_witch.send_text(
            "BODY 3", when=datetime.datetime.now(tz=datetime.timezone.utc)
        )

        with unittest.mock.patch("slidge.group.LegacyMUC.backfill") as backfill:
            self.run_coro(self.room._LegacyMUC__fill_history())
        backfill.assert_awaited_with(None, None)

    def test_live_with_id_before_backfill(self):
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        self.first_witch.send_text("BODY 2", 222, when=now)
        self.first_witch.send_text(
            "BODY 1", 111, when=now - datetime.timedelta(hours=1)
        )
        self.first_witch.send_text(
            "BODY 3", 333, when=now + datetime.timedelta(hours=1)
        )
        with unittest.mock.patch("slidge.group.LegacyMUC.backfill") as backfill:
            self.run_coro(self.room._LegacyMUC__fill_history())
        backfill.assert_awaited_once()
        after, before = backfill.call_args[0]
        assert before.id == 111
        assert before.timestamp == now - datetime.timedelta(hours=1)
        assert after is None

    def _add_back_filled_msg(self, legacy_id=None, when=None):
        self.xmpp.store.mam.add_message(
            self.room.pk,
            HistoryMessage(Message(), when),
            archive_only=True,
            legacy_msg_id=legacy_id,
        )

    def test_pre_backfilled_no_id(self):
        self._add_back_filled_msg()
        with unittest.mock.patch("slidge.group.LegacyMUC.backfill") as backfill:
            self.run_coro(self.room._LegacyMUC__fill_history())
        backfill.assert_awaited_with(None, None)

    def test_pre_backfilled_with_id(self):
        self._add_back_filled_msg(None)
        self._add_back_filled_msg(111)
        self._add_back_filled_msg(222)
        self._add_back_filled_msg(None)
        with unittest.mock.patch("slidge.group.LegacyMUC.backfill") as backfill:
            self.run_coro(self.room._LegacyMUC__fill_history())
        backfill.assert_awaited_once()
        after, before = backfill.call_args[0]
        assert before is None
        assert after is not None
        assert after.id == 222

    def test_pre_backfilled_with_id_and_live(self):
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        self._add_back_filled_msg(None, now - datetime.timedelta(days=5))
        self._add_back_filled_msg(111, now - datetime.timedelta(days=4))
        self._add_back_filled_msg(222, now - datetime.timedelta(days=3))
        self._add_back_filled_msg(None, now - datetime.timedelta(days=2))

        self.first_witch.send_text("BODY1", None)
        self.first_witch.send_text("BODY2", 555)
        self.first_witch.send_text("BODY3", None)
        self.first_witch.send_text("BODY5", 666)
        self.first_witch.send_text("BODY6", None)
        self.first_witch.send_text("BODY7", None)

        with unittest.mock.patch("slidge.group.LegacyMUC.backfill") as backfill:
            self.run_coro(self.room._LegacyMUC__fill_history())
        backfill.assert_awaited_once()
        after, before = backfill.call_args[0]
        assert before.id == 555
        assert after.id == 222
