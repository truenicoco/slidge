from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .. import config
from .base import BaseSender


class _NoChange(Exception):
    pass


@dataclass
class _CachedPresence:
    presence_kwargs: dict[str, str]
    last_seen: Optional[datetime] = None


class PresenceMixin(BaseSender):
    _last_presence: Optional[_CachedPresence] = None
    _ONLY_SEND_PRESENCE_CHANGES = False

    def _make_presence(
        self,
        *,
        last_seen: Optional[datetime] = None,
        force=False,
        bare=False,
        **presence_kwargs,
    ):
        old = self._last_presence

        if presence_kwargs.get("ptype") not in (
            "subscribe",
            "unsubscribe",
            "subscribed",
            "unsubscribed",
        ):
            self._last_presence = _CachedPresence(
                last_seen=last_seen, presence_kwargs=presence_kwargs
            )

        if old and not force and self._ONLY_SEND_PRESENCE_CHANGES:
            if old == self._last_presence:
                self.session.log.debug("Presence is the same as cached")
                raise _NoChange
            self.session.log.debug(
                "Presence is not the same as cached: %s vs %s", old, self._last_presence
            )

        p = self.xmpp.make_presence(
            pfrom=self.jid.bare if bare else self.jid, **presence_kwargs
        )
        if last_seen:
            if config.LAST_SEEN_FALLBACK and not presence_kwargs.get("pstatus"):
                p["status"] = f"Last seen {last_seen:%A %H:%M GMT}"
            if last_seen.tzinfo is None:
                last_seen = last_seen.astimezone(timezone.utc)
            p["idle"]["since"] = last_seen
        return p

    def send_last_presence(self, force=False):
        if (cache := self._last_presence) is None:
            if force:
                self.offline()
            return
        self._send(
            self._make_presence(
                last_seen=cache.last_seen, force=True, **cache.presence_kwargs
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
