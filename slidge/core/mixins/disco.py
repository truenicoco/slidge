import functools

from slidge.util.xep_0030.stanza.info import DiscoInfo

from .base import Base


class BaseDiscoMixin(Base):
    DISCO_TYPE: str = NotImplemented
    DISCO_CATEGORY: str = NotImplemented
    DISCO_NAME: str = NotImplemented
    DISCO_LANG = None

    def features(self):
        return []

    def extended_features(self):
        return

    def get_disco_info(self):
        info = DiscoInfo()
        for feature in self.features():
            info.add_feature(feature)
        info.add_identity(
            category=self.DISCO_CATEGORY,
            itype=self.DISCO_TYPE,
            name=self.DISCO_NAME,
            lang=self.DISCO_LANG,
        )
        if x := self.extended_features():
            info.append(x)
        return info


class ChatterDiscoMixin(BaseDiscoMixin):
    AVATAR = True
    RECEIPTS = True
    MARKS = True
    CHAT_STATES = True
    UPLOAD = True
    CORRECTION = True
    REACTION = True
    RETRACTION = True
    REPLIES = True

    DISCO_TYPE = "pc"
    DISCO_CATEGORY = "client"
    DISCO_NAME = ""

    def features(self):
        features = []
        if self.CHAT_STATES:
            features.append("http://jabber.org/protocol/chatstates")
        if self.RECEIPTS:
            features.append("urn:xmpp:receipts")
        if self.CORRECTION:
            features.append("urn:xmpp:message-correct:0")
        if self.MARKS:
            features.append("urn:xmpp:chat-markers:0")
        if self.UPLOAD:
            features.append("jabber:x:oob")
        if self.REACTION:
            features.append("urn:xmpp:reactions:0")
        if self.RETRACTION:
            features.append("urn:xmpp:message-retract:0")
        if self.REPLIES:
            features.append("urn:xmpp:reply:0")
        features.append("urn:ietf:params:xml:ns:vcard-4.0")
        return features

    async def update_caps(self):
        jid = self.jid
        xmpp = self.xmpp

        add_feature = functools.partial(xmpp["xep_0030"].add_feature, jid=jid)
        for f in self.features():
            await add_feature(f)

        await xmpp["xep_0115"].update_caps(jid=jid)
