from conftest import AvatarFixtureMixin

from slidge import BaseGateway, BaseSession
from slidge.contact import LegacyContact, LegacyRoster
from slidge.core.session import _sessions
from slidge.group import LegacyBookmarks
from slidge.util.test import SlidgeTest


class Gateway(BaseGateway):
    COMPONENT_NAME = "A test"


class Session(BaseSession):
    async def login(self):
        await self.contacts.by_name("some id", "some name")
        await self.bookmarks.by_name("some group id", "some group name")
        return "YUP"


class Roster(LegacyRoster):
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
        return

    async def by_name(self, legacy_id: str, name: str):
        muc = await self.by_legacy_id(legacy_id)
        muc.name = name


class TestSetNameBeforeFill(AvatarFixtureMixin, SlidgeTest):
    plugin = globals()

    def setUp(self):
        super().setUp()
        self.setup_logged_session(1)

    def tearDown(self):
        super().tearDown()
        _sessions.clear()

    def test_set_contact_name_before_fill(self):
        contact = self.run_coro(self.romeo.contacts.by_legacy_id("some id"))
        assert contact.name == "some name"
        muc = self.run_coro(self.romeo.bookmarks.by_legacy_id("some group id"))
        assert muc.name == "some group name"
