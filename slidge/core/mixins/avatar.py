from asyncio import Task, create_task
from hashlib import sha1
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from slixmpp import JID

from ...util.types import (
    URL,
    AnyBaseSession,
    AvatarIdType,
    AvatarType,
    LegacyFileIdType,
)
from ..cache import avatar_cache

if TYPE_CHECKING:
    from ..pubsub import PepAvatar


class AvatarMixin:
    """
    Mixin for XMPP entities that have avatars that represent them.

    Both :py:class:`slidge.LegacyContact` and :py:class:`slidge.LegacyMUC` use
    :py:class:`.AvatarMixin`.
    """

    jid: JID = NotImplemented
    session: AnyBaseSession = NotImplemented
    _avatar_pubsub_broadcast: bool = NotImplemented
    _avatar_bare_jid: bool = NotImplemented

    def __init__(self) -> None:
        super().__init__()
        self._set_avatar_task: Optional[Task] = None
        self.__avatar_unique_id: Optional[AvatarIdType] = None

    @property
    def __avatar_jid(self):
        return JID(self.jid.bare) if self._avatar_bare_jid else self.jid

    @property
    def avatar_id(self) -> Optional[AvatarIdType]:
        """
        The unique ID of this entity's avatar.
        """
        return self.__avatar_unique_id

    @property
    def avatar(self) -> Optional[AvatarIdType]:
        """
        This property can be used to set the avatar, but
        :py:meth:`~.AvatarMixin.set_avatar()` should be preferred because you can
        provide a unique ID for the avatar for efficient caching.
        Setting this is OKish in case the avatar type is a URL or a local path
        that can act as a legacy ID.

        Python's ``property`` is abused here to maintain backwards
        compatibility, but when getting it you actually get the avatar legacy
        ID.
        """
        return self.__avatar_unique_id

    @avatar.setter
    def avatar(self, a: Optional[AvatarType]):
        if self._set_avatar_task:
            self._set_avatar_task.cancel()
        self.session.log.debug("Setting avatar with property")
        self._set_avatar_task = self.session.xmpp.loop.create_task(
            self.set_avatar(a, None, blocking=True, cancel=False),
            name=f"Set avatar of {self} from property",
        )

    @staticmethod
    def __get_uid(a: Optional[AvatarType]) -> Optional[AvatarIdType]:
        if isinstance(a, str):
            return URL(a)
        elif isinstance(a, Path):
            return str(a)
        elif isinstance(a, bytes):
            return sha1(a).hexdigest()
        elif a is None:
            return None
        raise TypeError("Bad avatar", a)

    async def __set_avatar(self, a: Optional[AvatarType], uid: Optional[AvatarIdType]):
        self.__avatar_unique_id = uid
        await self.session.xmpp.pubsub.set_avatar(
            jid=self.__avatar_jid,
            avatar=a,
            unique_id=None if isinstance(uid, URL) else uid,
            broadcast_to=self.session.user.jid.bare,
            broadcast=self._avatar_pubsub_broadcast,
        )
        self._post_avatar_update()

    async def _no_change(self, a: Optional[AvatarType], uid: Optional[AvatarIdType]):
        if a is None:
            return self.__avatar_unique_id is None
        if not self.__avatar_unique_id:
            return False
        if isinstance(uid, URL):
            if self.__avatar_unique_id != uid:
                return False
            return not await avatar_cache.url_has_changed(uid)
        return self.__avatar_unique_id == uid

    async def set_avatar(
        self,
        a: Optional[AvatarType],
        avatar_unique_id: Optional[LegacyFileIdType] = None,
        blocking=False,
        cancel=True,
    ) -> None:
        """
        Set an avatar for this entity

        :param a:
        :param avatar_unique_id:
        :param blocking:
        :param cancel:
        """
        if avatar_unique_id is None and a is not None:
            avatar_unique_id = self.__get_uid(a)
        if await self._no_change(a, avatar_unique_id):
            return
        if cancel and self._set_avatar_task:
            self._set_avatar_task.cancel()
        awaitable = create_task(
            self.__set_avatar(a, avatar_unique_id),
            name=f"Set pubsub avatar of {self}",
        )
        if not self._set_avatar_task or self._set_avatar_task.done():
            self._set_avatar_task = awaitable
        if blocking:
            await awaitable

    def get_avatar(self) -> Optional["PepAvatar"]:
        if not self.__avatar_unique_id:
            return None
        return self.session.xmpp.pubsub.get_avatar(self.__avatar_jid)

    def _post_avatar_update(self) -> None:
        return

    async def avatar_wrap_update_info(self):
        cached_id = avatar_cache.get_cached_id_for(self.__avatar_jid)
        self.__avatar_unique_id = cached_id
        try:
            await self.update_info()  # type:ignore
        except NotImplementedError:
            return
        new_id = self.avatar
        if isinstance(new_id, URL) and not await avatar_cache.url_has_changed(new_id):
            return
        elif new_id != cached_id:
            # at this point it means that update_info set the avatar, and we don't
            # need to do anything else
            return

        await self.session.xmpp.pubsub.set_avatar_from_cache(
            self.__avatar_jid,
            new_id is None and cached_id is not None,
            self.session.user.jid.bare,
            self._avatar_pubsub_broadcast,
        )
