import logging
from typing import TYPE_CHECKING, NamedTuple, Optional

from slixmpp import JID, CoroutineCallback, Iq, StanzaPath
from slixmpp.plugins.base import BasePlugin, register_plugin
from slixmpp.plugins.xep_0292.stanza import NS, VCard4
from slixmpp.types import JidStr

from slidge.contact import LegacyContact

if TYPE_CHECKING:
    from slidge.core.gateway import BaseGateway


class StoredVCard(NamedTuple):
    content: VCard4
    authorized_jids: set[JidStr]


class VCard4Provider(BasePlugin):
    xmpp: "BaseGateway"

    name = "xep_0292_provider"
    description = "VCard4 Provider"
    dependencies = {"xep_0030"}

    def __init__(self, *a, **k):
        super(VCard4Provider, self).__init__(*a, **k)
        self._vcards = dict[JidStr, StoredVCard]()

    def plugin_init(self):
        self.xmpp.register_handler(
            CoroutineCallback(
                "get_vcard",
                StanzaPath(f"iq@type=get/vcard"),
                self.handle_vcard_get,  # type:ignore
            )
        )

        self.xmpp.plugin["xep_0030"].add_feature(NS)

    def _get_cached_vcard(self, jid: JidStr, requested_by: JidStr) -> Optional[VCard4]:
        vcard = self._vcards.get(JID(jid).bare)
        if vcard:
            if auth := vcard.authorized_jids:
                if JID(requested_by).bare in auth:
                    return vcard.content
            else:
                return vcard.content
        return None

    async def get_vcard(self, jid: JidStr, requested_by: JidStr) -> Optional[VCard4]:
        if vcard := self._get_cached_vcard(jid, requested_by):
            log.debug("Found a cached vcard")
            return vcard
        if not hasattr(self.xmpp, "get_session_from_jid"):
            return None
        jid = JID(jid)
        requested_by = JID(requested_by)
        session = self.xmpp.get_session_from_jid(requested_by)
        if session is None:
            return
        entity = await session.get_contact_or_group_or_participant(jid)
        if isinstance(entity, LegacyContact):
            log.debug("Fetching vcard")
            await entity.fetch_vcard()
            return self._get_cached_vcard(jid, requested_by)
        return None

    async def handle_vcard_get(self, iq: Iq):
        r = iq.reply()
        if vcard := await self.get_vcard(iq.get_to().bare, iq.get_from().bare):
            r.append(vcard)
        else:
            r.enable("vcard")
        r.send()

    def set_vcard(
        self,
        jid: JidStr,
        vcard: VCard4,
        /,
        authorized_jids: Optional[set[JidStr]] = None,
    ):
        cache = self._vcards.get(jid)
        new = StoredVCard(
            vcard, authorized_jids if authorized_jids is not None else set()
        )
        self._vcards[jid] = new
        if cache == new:
            return
        if self.xmpp["pubsub"] and authorized_jids:
            for to in authorized_jids:
                self.xmpp.loop.create_task(
                    self.xmpp["pubsub"].broadcast_vcard_event(jid, to)
                )


register_plugin(VCard4Provider)
log = logging.getLogger(__name__)
