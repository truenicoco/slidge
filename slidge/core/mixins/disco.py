from typing import Optional

from slixmpp.plugins.xep_0004 import Form
from slixmpp.plugins.xep_0030.stanza.info import DiscoInfo

from .base import Base


class BaseDiscoMixin(Base):
    DISCO_TYPE: str = NotImplemented
    DISCO_CATEGORY: str = NotImplemented
    DISCO_NAME: str = NotImplemented
    DISCO_LANG = None

    def __init__(self):
        super().__init__()
        self.__caps_cache: Optional[str] = None

    def features(self):
        return []

    async def extended_features(self) -> Optional[list[Form]]:
        return None

    async def get_disco_info(self):
        info = DiscoInfo()
        for feature in self.features():
            info.add_feature(feature)
        info.add_identity(
            category=self.DISCO_CATEGORY,
            itype=self.DISCO_TYPE,
            name=self.DISCO_NAME,
            lang=self.DISCO_LANG,
        )
        if forms := await self.extended_features():
            for form in forms:
                info.append(form)
        return info

    async def get_caps_ver(self):
        if self.__caps_cache:
            return self.__caps_cache
        info = await self.get_disco_info()
        caps = self.xmpp.plugin["xep_0115"]
        ver = caps.generate_verstring(info, caps.hash)
        self.__caps_cache = ver
        return ver

    def reset_caps_cache(self):
        self.__caps_cache = None


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

    async def extended_features(self):
        f = getattr(self, "restricted_emoji_extended_feature", None)
        if f is None:
            return

        e = await f()
        if not e:
            return

        return [e]
