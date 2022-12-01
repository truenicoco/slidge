from datetime import datetime, timezone
from typing import Optional

from ..core import config
from .base import BaseSender


class PresenceMixin(BaseSender):
    def _make_presence(
        self,
        *,
        last_seen: Optional[datetime] = None,
        **presence_kwargs,
    ):
        p = self.xmpp.make_presence(pfrom=self.jid, **presence_kwargs)
        if last_seen:
            if config.LAST_SEEN_FALLBACK and not presence_kwargs.get("pstatus"):
                p["status"] = f"Last seen {last_seen:%A %H:%M GMT}"
            if last_seen.tzinfo is None:
                last_seen = last_seen.astimezone(timezone.utc)
            p["idle"]["since"] = last_seen
        return p

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
        self._send(self._make_presence(pstatus=status, last_seen=last_seen))

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
        self._send(
            self._make_presence(pstatus=status, pshow="away", last_seen=last_seen)
        )

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
        self._send(self._make_presence(pstatus=status, pshow="xa", last_seen=last_seen))

    def busy(
        self,
        status: Optional[str] = None,
        last_seen: Optional[datetime] = None,
    ):
        """
        Send a "busy" presence from this contact to the user,

        :param status: eg: "Trying to make sense of XEP-0100"
        :param last_seen: For :xep:`0319`
        """
        self._send(
            self._make_presence(pstatus=status, pshow="busy", last_seen=last_seen)
        )

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
        self._send(
            self._make_presence(
                pstatus=status, pshow="unavailable", last_seen=last_seen
            )
        )
