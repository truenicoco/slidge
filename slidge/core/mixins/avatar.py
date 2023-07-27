from asyncio import Task, create_task
from hashlib import sha1
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from slixmpp import JID

from ...util.types import AnyBaseSession, AvatarType, LegacyFileIdType


class NoAvatarUpdate(Exception):
    pass


if TYPE_CHECKING:
    from ..pubsub import PepAvatar


class AvatarMixin:
    jid: JID = NotImplemented
    session: AnyBaseSession = NotImplemented
    _avatar_pubsub_broadcast: bool = NotImplemented
    _avatar_bare_jid: bool = NotImplemented

    def __init__(self) -> None:
        super().__init__()
        self._set_avatar_task: Optional[Task] = None
        self.__avatar_unique_id: Optional[LegacyFileIdType] = None

    @property
    def avatar(self):
        """
        The unique ID of the avatar that this contacts represent
        """
        return self.__avatar_unique_id

    @avatar.setter
    def avatar(self, a: Optional[AvatarType]):
        """
        Set the avatar. self.set_avatar() should be preferred because you can provide
        a unique ID for the avatar, to help caching.
        """
        uid = None if a is None else self.__get_uid(a)
        if self._no_change(a, uid):
            return
        if self._set_avatar_task:
            self._set_avatar_task.cancel()
        self._set_avatar_task = self.session.xmpp.loop.create_task(
            self.__set_avatar(a, uid),
            name=f"Set avatar of {self}",
        )

    @staticmethod
    def __get_uid(a: Optional[AvatarType]) -> Optional[LegacyFileIdType]:
        if isinstance(a, str):
            return a
        elif isinstance(a, Path):
            return str(a)
        elif isinstance(a, bytes):
            return sha1(a).hexdigest()
        elif a is None:
            return None
        raise TypeError("Bad avatar", a)

    async def __set_avatar(
        self, a: Optional[AvatarType], uid: Optional[LegacyFileIdType]
    ):
        self.__avatar_unique_id = uid
        await self.session.xmpp.pubsub.set_avatar(
            jid=self.jid.bare if self._avatar_bare_jid else self.jid,
            avatar=a,
            unique_id=uid,
            restrict_to=self.session.user.jid.bare,
            broadcast=self._avatar_pubsub_broadcast,
        )
        self._post_avatar_update()

    def _no_change(self, a: Optional[AvatarType], uid: Optional[LegacyFileIdType]):
        if a is None:
            return self.__avatar_unique_id is None
        if not self.__avatar_unique_id:
            return False
        return self.__avatar_unique_id == uid

    async def set_avatar(
        self,
        a: Optional[AvatarType],
        avatar_unique_id: Optional[LegacyFileIdType] = None,
        blocking=False,
    ):
        if avatar_unique_id is None and a is not None:
            avatar_unique_id = self.__get_uid(a)
        if self._no_change(a, avatar_unique_id):
            return
        if self._set_avatar_task:
            self._set_avatar_task.cancel()
        awaitable = self._set_avatar_task = create_task(
            self.__set_avatar(a, avatar_unique_id),
            name=f"Set pubsub avatar of {self}",
        )
        if blocking:
            await awaitable

    def get_avatar(self) -> Optional["PepAvatar"]:
        if not self.__avatar_unique_id:
            return None
        return self.session.xmpp.pubsub.get_avatar(jid=self.jid.bare)

    def _post_avatar_update(self) -> None:
        return

    async def fetch_avatar(
        self, unique_id: Optional[LegacyFileIdType] = None
    ) -> Optional[AvatarType]:
        raise NotImplementedError
