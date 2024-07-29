from asyncio import Task, create_task
from hashlib import sha1
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from slixmpp import JID

from ...db.avatar import CachedAvatar, avatar_cache
from ...util.types import (
    URL,
    AnyBaseSession,
    AvatarIdType,
    AvatarType,
    LegacyFileIdType,
)

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
    _avatar_bare_jid: bool = NotImplemented

    def __init__(self) -> None:
        super().__init__()
        self._set_avatar_task: Optional[Task] = None
        self.__broadcast_task: Optional[Task] = None
        self.__avatar_unique_id: Optional[AvatarIdType] = None
        self._avatar_pk: Optional[int] = None

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

    @property
    def avatar_pk(self) -> int | None:
        return self._avatar_pk

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

    async def __set_avatar(
        self, a: Optional[AvatarType], uid: Optional[AvatarIdType], delete: bool
    ):
        self.__avatar_unique_id = uid

        if a is None:
            cached_avatar = None
            self._avatar_pk = None
        else:
            try:
                cached_avatar = await avatar_cache.convert_or_get(a)
            except Exception as e:
                self.session.log.error("Failed to set avatar %s", a, exc_info=e)
                self._avatar_pk = None
                self.__avatar_unique_id = uid
                return
            self._avatar_pk = cached_avatar.pk

        if self.__should_pubsub_broadcast():
            await self.session.xmpp.pubsub.broadcast_avatar(
                self.__avatar_jid, self.session.user_jid, cached_avatar
            )

        if delete and isinstance(a, Path):
            a.unlink()

        self._post_avatar_update()

    def __should_pubsub_broadcast(self):
        return getattr(self, "is_friend", False) and getattr(
            self, "added_to_roster", False
        )

    async def _no_change(self, a: Optional[AvatarType], uid: Optional[AvatarIdType]):
        if a is None:
            return self.__avatar_unique_id is None
        if not self.__avatar_unique_id:
            return False
        if isinstance(uid, URL):
            if self.__avatar_unique_id != uid:
                return False
            return not await avatar_cache.url_modified(uid)
        return self.__avatar_unique_id == uid

    async def set_avatar(
        self,
        a: Optional[AvatarType],
        avatar_unique_id: Optional[LegacyFileIdType] = None,
        delete: bool = False,
        blocking=False,
        cancel=True,
    ) -> None:
        """
        Set an avatar for this entity

        :param a: The avatar, in one of the types slidge supports
        :param avatar_unique_id: A globally unique ID for the avatar on the
            legacy network
        :param delete: If the avatar is provided as a Path, whether to delete
            it once used or not.
        :param blocking: Internal use by slidge for tests, do not use!
        :param cancel: Internal use by slidge, do not use!
        """
        if avatar_unique_id is None and a is not None:
            avatar_unique_id = self.__get_uid(a)
        if await self._no_change(a, avatar_unique_id):
            return
        if cancel and self._set_avatar_task:
            self._set_avatar_task.cancel()
        awaitable = create_task(
            self.__set_avatar(a, avatar_unique_id, delete),
            name=f"Set pubsub avatar of {self}",
        )
        if not self._set_avatar_task or self._set_avatar_task.done():
            self._set_avatar_task = awaitable
        if blocking:
            await awaitable

    def get_cached_avatar(self) -> Optional["CachedAvatar"]:
        if self._avatar_pk is None:
            return None
        return avatar_cache.get_by_pk(self._avatar_pk)

    def get_avatar(self) -> Optional["PepAvatar"]:
        cached_avatar = self.get_cached_avatar()
        if cached_avatar is None:
            return None
        from ..pubsub import PepAvatar

        item = PepAvatar()
        item.set_avatar_from_cache(cached_avatar)
        return item

    def _post_avatar_update(self) -> None:
        return

    def __get_cached_avatar_id(self):
        i = self._get_cached_avatar_id()
        if i is None:
            return None
        return self.session.xmpp.AVATAR_ID_TYPE(i)

    def _get_cached_avatar_id(self) -> Optional[str]:
        raise NotImplementedError

    async def avatar_wrap_update_info(self):
        cached_id = self.__get_cached_avatar_id()
        self.__avatar_unique_id = cached_id
        try:
            await self.update_info()  # type:ignore
        except NotImplementedError:
            return
        new_id = self.avatar
        if isinstance(new_id, URL) and not await avatar_cache.url_modified(new_id):
            return
        elif new_id != cached_id:
            # at this point it means that update_info set the avatar, and we don't
            # need to do anything else
            return

        if self.__should_pubsub_broadcast():
            if new_id is None and cached_id is None:
                return
            if self._avatar_pk is not None:
                cached_avatar = avatar_cache.get_by_pk(self._avatar_pk)
            else:
                cached_avatar = None
            self.__broadcast_task = self.session.xmpp.loop.create_task(
                self.session.xmpp.pubsub.broadcast_avatar(
                    self.__avatar_jid, self.session.user_jid, cached_avatar
                )
            )

    def _set_avatar_from_store(self, stored):
        if stored.avatar_id is None:
            return
        if stored.avatar is None:
            # seems to happen after avatar cleanup for some reason?
            self.__avatar_unique_id = None
            return
        self.__avatar_unique_id = (
            stored.avatar.legacy_id
            if stored.avatar.legacy_id is not None
            else URL(stored.avatar.url)
        )
