from typing import AsyncIterator

from conftest import AvatarFixtureMixin

from slidge import BaseGateway, BaseSession
from slidge.contact import LegacyContact, LegacyRoster
from slidge.core.session import _sessions
from slidge.group import LegacyBookmarks
from slidge.util.test import SlidgeTest


class Gateway(BaseGateway):
    COMPONENT_NAME = "A test"
    GROUPS = True


class Session(BaseSession):
    async def login(self):
        return "YUP"


class Roster(LegacyRoster):
    async def fill(self) -> AsyncIterator["Contact"]:
        yield await self.by_name("some id", "some name")

    async def by_name(self, legacy_id: str, name: str):
        return await self.by_legacy_id(legacy_id, name)


class Contact(LegacyContact):
    def __init__(self, session, legacy_id, jid_username, name: str | None = None):
        super().__init__(session, legacy_id, jid_username)
        if name is not None:
            self.name = name

    def use_contact_info(self, name: str):
        self.name = name


class Bookmarks(LegacyBookmarks):
    async def fill(self):
        muc = await self.by_legacy_id("some group id")
        muc.name = "some group name"


class TestSetContactNameInConstructor(AvatarFixtureMixin, SlidgeTest):
    plugin = globals()

    def setUp(self):
        super().setUp()
        self.setup_logged_session(1)

    def tearDown(self):
        super().tearDown()
        _sessions.clear()

    def test_set_name_in_constructor(self):
        contact = self.run_coro(self.romeo.contacts.by_legacy_id("some id"))
        assert contact.name == "some name"
        muc = self.run_coro(self.romeo.bookmarks.by_legacy_id("some group id"))
        assert muc.name == "some group name"

    def test_participant(self):
        muc = self.run_coro(self.romeo.bookmarks.by_legacy_id("some group id"))
        participant = self.run_coro(muc.get_participant_by_legacy_id("some other id"))
        assert participant.nickname == "some other id"
