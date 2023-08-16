import re
from datetime import datetime, timezone
from typing import Optional

from slixmpp.types import PresenceTypes

from ...util.sql import CachedPresence, db
from ...util.types import PresenceShow
from .. import config
from .base import BaseSender


class _NoChange(Exception):
    pass


_FRIEND_REQUEST_PRESENCES = {"subscribe", "unsubscribe", "subscribed", "unsubscribed"}


class PresenceMixin(BaseSender):
    _ONLY_SEND_PRESENCE_CHANGES = False

    def _get_last_presence(self) -> Optional[CachedPresence]:
        return db.presence_get(self.jid, self.user)

    def _store_last_presence(self, new: CachedPresence):
        return db.presence_store(self.jid, new, self.user)

    def _make_presence(
        self,
        *,
        last_seen: Optional[datetime] = None,
        force=False,
        bare=False,
        ptype: Optional[PresenceTypes] = None,
        pstatus: Optional[str] = None,
        pshow: Optional[PresenceShow] = None,
    ):
        old = self._get_last_presence()

        if ptype not in _FRIEND_REQUEST_PRESENCES:
            new = CachedPresence(
                last_seen=last_seen, ptype=ptype, pstatus=pstatus, pshow=pshow
            )
            if old != new:
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
            if last_seen.tzinfo is None:
                last_seen = last_seen.astimezone(timezone.utc)
            # it's ugly to check for the presence of this string, but a better fix is more work
            if config.LAST_SEEN_FALLBACK and not re.match(
                ".*Last seen .* GMT", p["status"]
            ):
                last_seen_fallback = f"Last seen {last_seen:%A %H:%M GMT}"
                if p["status"]:
                    p["status"] = p["status"] + " -- " + last_seen_fallback
                else:
                    p["status"] = last_seen_fallback
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
