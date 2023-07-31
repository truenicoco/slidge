from pathlib import Path

import pytest
from PIL.Image import Image
from test_shakespeare import Base as BaseNoMUC

from slidge import LegacyMUC, MucType
from slidge.core.cache import avatar_cache


# just to have typings for the fixture which pycharm does not understand
class Avatar:
    avatar_path: Path
    avatar_image: Image
    avatar_bytes: bytes
    avatar_sha1: str
    avatar_original_sha1: str


@pytest.mark.usefixtures("avatar")
class TestContactAvatar(BaseNoMUC, Avatar):
    avatar_path: Path
    avatar_image: Image
    avatar_bytes: bytes
    avatar_sha1: str
    avatar_original_sha1: str

    def setUp(self):
        super().setUp()
        self.juliet.is_friend = True

    def __assert_not_found(self):
        juliet = self.juliet
        self.recv(  # language=XML
            f"""
            <iq type='get'
                from='{juliet.user.jid}/client'
                to='{juliet.jid.bare}'
                id='retrieve1'>
              <pubsub xmlns='http://jabber.org/protocol/pubsub'>
                <items node='urn:xmpp:avatar:data'>
                  <item id='{self.avatar_sha1}' />
                </items>
              </pubsub>
            </iq>
            """
        )
        self.send(  # language=XML
            f"""
            <iq xmlns="jabber:component:accept"
                type="error"
                from="juliet@aim.shakespeare.lit"
                to="{juliet.user.jid}/client"
                id="retrieve1">
              <error type="cancel">
                <item-not-found xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
              </error>
            </iq>
            """
        )

    def __assert_publish(self, rewritten=False):
        h = self.avatar_sha1 if rewritten else self.avatar_original_sha1
        length = (
            len(self.avatar_bytes) if rewritten else len(self.avatar_path.read_bytes())
        )
        self.send(  # language=XML
            f"""
            <message type="headline"
                     from="juliet@aim.shakespeare.lit"
                     to="romeo@montague.lit">
              <event xmlns="http://jabber.org/protocol/pubsub#event">
                <items node="urn:xmpp:avatar:metadata">
                  <item id="{h}">
                    <metadata xmlns="urn:xmpp:avatar:metadata">
                      <info id="{h}"
                            type="image/png"
                            bytes="{length}"
                            height="5"
                            width="5" />
                    </metadata>
                  </item>
                </items>
              </event>
            </message>
            """
        )
        assert self.next_sent() is None

    def __assert_publish_empty(self):
        self.send(  # language=XML
            f"""
            <message type="headline"
                     from="juliet@aim.shakespeare.lit"
                     to="romeo@montague.lit">
              <event xmlns="http://jabber.org/protocol/pubsub#event">
                <items node="urn:xmpp:avatar:metadata">
                  <item>
                    <metadata xmlns="urn:xmpp:avatar:metadata" />
                  </item>
                </items>
              </event>
            </message>
            """,
            use_values=False,
        )
        assert self.next_sent() is None

    def test_avatar_path_no_id(self):
        juliet = self.juliet
        assert juliet.avatar is None

        juliet.avatar = None
        assert self.next_sent() is None

        self.__assert_not_found()

        juliet.avatar = self.avatar_path
        self.run_coro(juliet._set_avatar_task)
        self.__assert_publish()

        juliet.avatar = self.avatar_path
        self.run_coro(juliet._set_avatar_task)
        assert self.next_sent() is None

        self.run_coro(juliet.set_avatar(self.avatar_path))
        assert self.next_sent() is None

        self.run_coro(juliet.set_avatar(self.avatar_path))
        assert self.next_sent() is None

        juliet.avatar = self.avatar_path
        self.run_coro(juliet._set_avatar_task)
        assert self.next_sent() is None

        juliet.avatar = None
        self.run_coro(juliet._set_avatar_task)
        self.__assert_publish_empty()

        self.run_coro(juliet.set_avatar(None))
        assert self.next_sent() is None

        self.run_coro(juliet.set_avatar(self.avatar_path))
        self.__assert_publish()

        juliet.avatar = None
        self.run_coro(juliet._set_avatar_task)
        self.__assert_publish_empty()

    def test_avatar_path_with_id(self):
        juliet = self.juliet
        assert juliet.avatar is None

        self.run_coro(juliet.set_avatar(self.avatar_path, 123))
        self.__assert_publish(rewritten=True)

        assert avatar_cache.get_cached_id_for(juliet.jid.bare) == 123

        self.run_coro(juliet.set_avatar(self.avatar_path, 123))
        assert self.next_sent() is None

        assert avatar_cache.get_cached_id_for(juliet.jid.bare) == 123

        self.run_coro(juliet.set_avatar(self.avatar_path, "123"))
        self.__assert_publish(rewritten=True)
        assert avatar_cache.get_cached_id_for(juliet.jid.bare) == "123"

        self.run_coro(juliet.set_avatar(None))
        self.__assert_publish_empty()

        assert avatar_cache.get_cached_id_for(juliet.jid.bare) is None

    def test_avatar_with_url(self):
        juliet = self.juliet
        assert juliet.avatar is None
        juliet.avatar = "https://nicoco.fr/5x5.png"
        self.run_coro(juliet._set_avatar_task)
        self.__assert_publish(rewritten=True)

        juliet.avatar = "https://nicoco.fr/5x5.png"
        self.run_coro(juliet._set_avatar_task)
        assert self.next_sent() is None


class MUC(LegacyMUC):
    type = MucType.GROUP
    user_nick = "romeo"


class BaseMUC(BaseNoMUC):
    plugin = BaseNoMUC.plugin | {"MUC": MUC}

    def _assert_send_room_avatar(self, empty=False, url=False):
        if empty:
            photo = "<photo />"
        else:
            photo = f"<photo>{self.avatar_sha1 if url else self.avatar_original_sha1}</photo>"
        self.send(  # language=XML
            f"""
            <presence to="romeo@montague.lit/gajim"
                      from="room@aim.shakespeare.lit">
              <x xmlns="vcard-temp:x:update">{photo}</x>
            </presence>
            """,
            use_values=not empty,
        )

    def romeo_joins(self, muc: MUC):
        session = self.get_romeo_session()
        self.recv(  # language=XML
            f"""
            <presence from="{session.user.jid}/gajim"
                      to="room@{session.xmpp.boundjid.bare}/romeo">
              <x xmlns='http://jabber.org/protocol/muc' />
            </presence>
            """
        )
        self.send(  # language=XML
            """
            <presence from="room@aim.shakespeare.lit/romeo"
                      to="romeo@montague.lit/gajim">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="member"
                      role="participant"
                      jid="romeo@montague.lit/gajim" />
                <status code="100" />
                <status code="110" />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="slidge-user" />
            </presence>
            """
        )
        assert self.next_sent()["subject"] != ""
        # assert self.next_sent()["from"] == "room@aim.shakespeare.lit"

    def get_muc(self, joined=True) -> MUC:
        session = self.get_romeo_session()
        muc = self.run_coro(session.bookmarks.by_legacy_id("room"))
        if joined:
            self.romeo_joins(muc)
        return muc


@pytest.mark.usefixtures("avatar")
class TestParticipantAvatar(BaseMUC, Avatar):
    def romeo_joins(self, muc: MUC):
        super().romeo_joins(muc)
        self._assert_send_room_avatar(empty=True)

    def _assert_juliet_presence_no_avatar(self):
        self.send(  # language=XML
            """
            <presence from="room@aim.shakespeare.lit/juliet"
                      to="romeo@montague.lit/gajim">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="member"
                      role="participant"
                      jid="juliet@aim.shakespeare.lit/slidge" />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="juliet@aim.shakespeare.lit/slidge" />
            </presence>
            """
        )

    def _assert_juliet_presence_avatar(self, sha=None, url=False):
        self.send(  # language=XML
            f"""
            <presence from="room@aim.shakespeare.lit/juliet"
                      to="romeo@montague.lit/gajim">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="member"
                      role="participant"
                      jid="juliet@aim.shakespeare.lit/slidge" />
              </x>
              <x xmlns="vcard-temp:x:update">
                <photo>{self.avatar_sha1 if url else self.avatar_original_sha1}</photo>
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="juliet@aim.shakespeare.lit/slidge" />
            </presence>
            """
        )

    def test_romeo_join_empty_room_then_juliet_joins_then_set_avatar(self):
        muc = self.get_muc(joined=True)
        session = self.get_romeo_session()

        session.contacts.ready.set_result(True)
        juliet = self.juliet
        self.run_coro(muc.get_participant_by_contact(juliet))
        self._assert_juliet_presence_no_avatar()
        assert self.next_sent() is None

        juliet.avatar = self.avatar_path
        # no broadcast of the contact avatar because not added to roster,
        # only the participant
        self.run_coro(juliet._set_avatar_task)
        self._assert_juliet_presence_avatar()
        assert self.next_sent() is None

        juliet.avatar = self.avatar_path
        assert self.next_sent() is None

        juliet.avatar = None
        self.run_coro(juliet._set_avatar_task)
        self._assert_juliet_presence_no_avatar()
        assert self.next_sent() is None

    def test_romeo_join_empty_room_then_juliet_joins_then_set_avatar_with_url(self):
        muc = self.get_muc(joined=True)
        session = self.get_romeo_session()

        session.contacts.ready.set_result(True)
        juliet = self.juliet
        self.run_coro(muc.get_participant_by_contact(juliet))
        self._assert_juliet_presence_no_avatar()
        assert self.next_sent() is None

        juliet.avatar = "https://nicoco.fr/5x5.png"
        # no broadcast of the contact avatar because not added to roster,
        # only the participant
        self.run_coro(juliet._set_avatar_task)
        self._assert_juliet_presence_avatar(url=True)
        assert self.next_sent() is None

        juliet.avatar = "https://nicoco.fr/5x5.png"
        self.run_coro(juliet._set_avatar_task)
        assert self.next_sent() is None

        juliet.avatar = None
        self.run_coro(juliet._set_avatar_task)
        self._assert_juliet_presence_no_avatar()
        assert self.next_sent() is None


@pytest.mark.usefixtures("avatar")
class TestRoomAvatar(BaseMUC, Avatar):
    def test_room_avatar_change_after_join(self):
        muc = self.get_muc(joined=True)
        self._assert_send_room_avatar(empty=True)
        muc.avatar = self.avatar_path
        self.run_coro(muc._set_avatar_task)
        self._assert_send_room_avatar()

    def test_room_avatar_on_join(self):
        muc = self.get_muc(joined=False)
        muc.avatar = self.avatar_path
        self.romeo_joins(muc)
        self._assert_send_room_avatar()

    def test_room_avatar_with_url(self):
        muc = self.get_muc(joined=False)
        muc.avatar = "https://nicoco.fr/5x5.png"
        self.run_coro(muc._set_avatar_task)
        self.romeo_joins(muc)
        self._assert_send_room_avatar(url=True)