from slixmpp import CoroutineCallback, Iq, StanzaPath

from .util import DispatcherMixin, exceptions_to_xmpp_errors


class VCardMixin(DispatcherMixin):
    def __init__(self, xmpp):
        super().__init__(xmpp)
        self.xmpp.register_handler(
            CoroutineCallback(
                "get_vcard", StanzaPath("iq@type=get/vcard"), self.on_get_vcard
            )
        )

    @exceptions_to_xmpp_errors
    async def on_get_vcard(self, iq: Iq):
        session = await self._get_session(iq)
        session.raise_if_not_logged()
        contact = await session.contacts.by_jid(iq.get_to())
        vcard = await contact.get_vcard()
        reply = iq.reply()
        if vcard:
            reply.append(vcard)
        else:
            reply.enable("vcard")
        reply.send()
