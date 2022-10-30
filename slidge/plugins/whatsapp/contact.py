from datetime import datetime

from slixmpp.exceptions import XMPPError

from slidge import LegacyContact, LegacyRoster
from slidge.plugins.whatsapp.generated import whatsapp


class Contact(LegacyContact):
    # WhatsApp only allows message editing in Beta versions of their app, and support is uncertain.
    CORRECTION = False

    def update_presence(self, away: bool, last_seen_timestamp: int):
        last_seen = (
            datetime.fromtimestamp(last_seen_timestamp)
            if last_seen_timestamp > 0
            else None
        )
        if away:
            self.away(last_seen=last_seen)
        else:
            self.online(last_seen=last_seen)


class Roster(LegacyRoster):
    @staticmethod
    def legacy_id_to_jid_username(legacy_id: str) -> str:
        return "+" + legacy_id[: legacy_id.find("@")]

    @staticmethod
    async def jid_username_to_legacy_id(jid_username: str) -> int:
        try:
            return jid_username.removeprefix("+") + "@" + whatsapp.DefaultUserServer
        except ValueError:
            raise XMPPError("bad-request")
