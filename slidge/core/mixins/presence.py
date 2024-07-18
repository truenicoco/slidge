import re
from asyncio import Task, sleep
from datetime import datetime, timedelta, timezone
from typing import Optional

from slixmpp.types import PresenceShows, PresenceTypes

from ...util.types import CachedPresence
from .. import config
from .base import BaseSender


class _NoChange(Exception):
    pass


_FRIEND_REQUEST_PRESENCES = {"subscribe", "unsubscribe", "subscribed", "unsubscribed"}


class PresenceMixin(BaseSender):
    _ONLY_SEND_PRESENCE_CHANGES = False
    contact_pk: Optional[int] = None

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        # FIXME: this should not be an attribute of this mixin to allow garbage
        #        collection of instances
        self.__update_last_seen_fallback_task: Optional[Task] = None
        # this is only used when a presence is set during Contact.update_info(),
        # when the contact does not have a DB primary key yet, and is written
        # to DB at the end of update_info()
        self.cached_presence: Optional[CachedPresence] = None

    async def __update_last_seen_fallback(self):
        await sleep(3600 * 7)
        self.send_last_presence(force=True, no_cache_online=False)

    def _get_last_presence(self) -> Optional[CachedPresence]:
        if self.contact_pk is None:
            return None
        return self.xmpp.store.contacts.get_presence(self.contact_pk)

    def _store_last_presence(self, new: CachedPresence):
        if self.contact_pk is None:
            self.cached_presence = new
            return
        self.xmpp.store.contacts.set_presence(self.contact_pk, new)

    def _make_presence(
        self,
        *,
        last_seen: Optional[datetime] = None,
        force=False,
        bare=False,
        ptype: Optional[PresenceTypes] = None,
        pstatus: Optional[str] = None,
        pshow: Optional[PresenceShows] = None,
    ):
        if last_seen and last_seen.tzinfo is None:
            last_seen = last_seen.astimezone(timezone.utc)

        old = self._get_last_presence()

        if ptype not in _FRIEND_REQUEST_PRESENCES:
            new = CachedPresence(
                last_seen=last_seen, ptype=ptype, pstatus=pstatus, pshow=pshow
            )
            if old != new:
                if hasattr(self, "muc") and ptype == "unavailable":
                    if self.contact_pk is not None:
                        self.xmpp.store.contacts.reset_presence(self.contact_pk)
                else:
                    self._store_last_presence(new)
            if old and not force and self._ONLY_SEND_PRESENCE_CHANGES:
                if old == new:
                    self.session.log.debug("Presence is the same as cached")
                    raise _NoChange
                self.session.log.debug(
                    "Presence is not the same as cached: %s vs %s", old, new
                )

        p = self.xmpp.make_presence(
            pfrom=self.jid.bare if bare else self.jid,
            ptype=ptype,
            pshow=pshow,
            pstatus=pstatus,
        )
        if last_seen:
            # it's ugly to check for the presence of this string, but a better fix is more work
            if config.LAST_SEEN_FALLBACK and not re.match(
                ".*Last seen .*", p["status"]
            ):
                last_seen_fallback, recent = get_last_seen_fallback(last_seen)
                if p["status"]:
                    p["status"] = p["status"] + " -- " + last_seen_fallback
                else:
                    p["status"] = last_seen_fallback
                if recent:
                    # if less than a week, we use sth like 'Last seen: Monday, 8:05",
                    # but if lasts more than a week, this is not very informative, so
                    # we need to force resend an updated presence status
                    if self.__update_last_seen_fallback_task:
                        self.__update_last_seen_fallback_task.cancel()
                    self.__update_last_seen_fallback_task = self.xmpp.loop.create_task(
                        self.__update_last_seen_fallback()
                    )
            p["idle"]["since"] = last_seen
        return p

    def send_last_presence(self, force=False, no_cache_online=False):
        if (cache := self._get_last_presence()) is None:
            if force:
                if no_cache_online:
                    self.online()
                else:
                    self.offline()
            return
        self._send(
            self._make_presence(
                last_seen=cache.last_seen,
                force=True,
                ptype=cache.ptype,
                pshow=cache.pshow,
                pstatus=cache.pstatus,
            )
        )

    def online(
        self,
        status: Optional[str] = None,
        last_seen: Optional[datetime] = None,
    ):
        """
        Send an "online" presence from this contact to the user.

        :param status: Arbitrary text, details of the status, eg: "Listening to Britney Spears"
        :param last_seen: For :xep:`0319`
        """
        try:
            self._send(self._make_presence(pstatus=status, last_seen=last_seen))
        except _NoChange:
            pass

    def away(
        self,
        status: Optional[str] = None,
        last_seen: Optional[datetime] = None,
    ):
        """
        Send an "away" presence from this contact to the user.

        This is a global status, as opposed to :meth:`.LegacyContact.inactive`
        which concerns a specific conversation, ie a specific "chat window"

        :param status: Arbitrary text, details of the status, eg: "Gone to fight capitalism"
        :param last_seen: For :xep:`0319`
        """
        try:
            self._send(
                self._make_presence(pstatus=status, pshow="away", last_seen=last_seen)
            )
        except _NoChange:
            pass

    def extended_away(
        self,
        status: Optional[str] = None,
        last_seen: Optional[datetime] = None,
    ):
        """
        Send an "extended away" presence from this contact to the user.

        This is a global status, as opposed to :meth:`.LegacyContact.inactive`
        which concerns a specific conversation, ie a specific "chat window"

        :param status: Arbitrary text, details of the status, eg: "Gone to fight capitalism"
        :param last_seen: For :xep:`0319`
        """
        try:
            self._send(
                self._make_presence(pstatus=status, pshow="xa", last_seen=last_seen)
            )
        except _NoChange:
            pass

    def busy(
        self,
        status: Optional[str] = None,
        last_seen: Optional[datetime] = None,
    ):
        """
        Send a "busy" (ie, "dnd") presence from this contact to the user,

        :param status: eg: "Trying to make sense of XEP-0100"
        :param last_seen: For :xep:`0319`
        """
        try:
            self._send(
                self._make_presence(pstatus=status, pshow="dnd", last_seen=last_seen)
            )
        except _NoChange:
            pass

    def offline(
        self,
        status: Optional[str] = None,
        last_seen: Optional[datetime] = None,
    ):
        """
        Send an "offline" presence from this contact to the user.

        :param status: eg: "Trying to make sense of XEP-0100"
        :param last_seen: For :xep:`0319`
        """
        try:
            self._send(
                self._make_presence(
                    pstatus=status, ptype="unavailable", last_seen=last_seen
                )
            )
        except _NoChange:
            pass


def get_last_seen_fallback(last_seen: datetime):
    now = datetime.now(tz=timezone.utc)
    if now - last_seen < timedelta(days=7):
        return f"Last seen {last_seen:%A %H:%M GMT}", True
    else:
        return f"Last seen {last_seen:%b %-d %Y}", False
