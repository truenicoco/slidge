import logging
from dataclasses import dataclass, field
from typing import Optional

from slixmpp import JID, ComponentXMPP, CoroutineCallback, Iq, StanzaPath
from slixmpp.plugins.base import BasePlugin, register_plugin
from slixmpp.types import JidStr

from .stanza import NS, VCard4


@dataclass
class StoredVCard:
    content: VCard4
    authorized_jids: set[JidStr] = field(default_factory=set)


class VCard4Provider(BasePlugin):
    xmpp: ComponentXMPP

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

    def get_vcard(self, jid: JidStr, requested_by: JidStr) -> Optional[VCard4]:
        vcard = self._vcards.get(JID(jid).bare)
        if vcard:
            if auth := vcard.authorized_jids:
                if JID(requested_by).bare in auth:
                    return vcard.content
            else:
                return vcard.content
        return None

    async def handle_vcard_get(self, iq: Iq):
        r = iq.reply()
        if vcard := self.get_vcard(iq.get_to().bare, iq.get_from().bare):
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
        self._vcards[jid] = StoredVCard(
            vcard, authorized_jids if authorized_jids is not None else set()
        )
        if self.xmpp["pubsub"] and authorized_jids:
            for to in authorized_jids:
                self.xmpp["pubsub"].broadcast_vcard_event(jid, to)


register_plugin(VCard4Provider)
log = logging.getLogger(__name__)
