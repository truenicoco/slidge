import logging
from copy import copy
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

from slixmpp import (
    JID,
    CoroutineCallback,
    Iq,
    Presence,
    StanzaPath,
    register_stanza_plugin,
)
from slixmpp.exceptions import XMPPError
from slixmpp.plugins.base import BasePlugin, register_plugin
from slixmpp.plugins.xep_0060.stanza import Event, EventItem, EventItems, Item
from slixmpp.plugins.xep_0084 import Data as AvatarData
from slixmpp.plugins.xep_0084 import MetaData as AvatarMetadata
from slixmpp.plugins.xep_0172 import UserNick
from slixmpp.plugins.xep_0292.stanza import VCard4
from slixmpp.types import JidStr, OptJidStr

from ..db.avatar import CachedAvatar, avatar_cache
from ..db.store import ContactStore, SlidgeStore
from .mixins.lock import NamedLockMixin

if TYPE_CHECKING:
    from slidge.core.gateway import BaseGateway

    from ..contact.contact import LegacyContact

VCARD4_NAMESPACE = "urn:xmpp:vcard4"


class PepItem:
    pass


class PepAvatar(PepItem):
    store: SlidgeStore

    def __init__(self):
        self.metadata: Optional[AvatarMetadata] = None
        self.id: Optional[str] = None
        self._avatar_data_path: Optional[Path] = None

    @property
    def data(self) -> Optional[AvatarData]:
        if self._avatar_data_path is None:
            return None
        data = AvatarData()
        data.set_value(self._avatar_data_path.read_bytes())
        return data

    def set_avatar_from_cache(self, cached_avatar: CachedAvatar):
        metadata = AvatarMetadata()
        self.id = cached_avatar.hash
        metadata.add_info(
            id=cached_avatar.hash,
            itype="image/png",
            ibytes=cached_avatar.path.stat().st_size,
            height=str(cached_avatar.height),
            width=str(cached_avatar.width),
        )
        self.metadata = metadata
        self._avatar_data_path = cached_avatar.path


class PepNick(PepItem):
    contact_store: ContactStore

    def __init__(self, nick: Optional[str] = None):
        nickname = UserNick()
        if nick is not None:
            nickname["nick"] = nick
        self.nick = nickname
        self.__nick_str = nick


class PubSubComponent(NamedLockMixin, BasePlugin):
    xmpp: "BaseGateway"

    name = "pubsub"
    description = "Pubsub component"
    dependencies = {
        "xep_0030",
        "xep_0060",
        "xep_0115",
        "xep_0163",
    }
    default_config = {"component_name": None}
    component_name: str

    def __init__(self, *a, **kw):
        super(PubSubComponent, self).__init__(*a, **kw)
        register_stanza_plugin(EventItem, UserNick)

    def plugin_init(self):
        self.xmpp.register_handler(
            CoroutineCallback(
                "pubsub_get_avatar_data",
                StanzaPath(f"iq@type=get/pubsub/items@node={AvatarData.namespace}"),
                self._get_avatar_data,  # type:ignore
            )
        )
        self.xmpp.register_handler(
            CoroutineCallback(
                "pubsub_get_avatar_metadata",
                StanzaPath(f"iq@type=get/pubsub/items@node={AvatarMetadata.namespace}"),
                self._get_avatar_metadata,  # type:ignore
            )
        )
        self.xmpp.register_handler(
            CoroutineCallback(
                "pubsub_get_vcard",
                StanzaPath(f"iq@type=get/pubsub/items@node={VCARD4_NAMESPACE}"),
                self._get_vcard,  # type:ignore
            )
        )

        disco = self.xmpp.plugin["xep_0030"]
        disco.add_identity("pubsub", "pep", self.component_name)
        disco.add_identity("account", "registered", self.component_name)
        disco.add_feature("http://jabber.org/protocol/pubsub#event")
        disco.add_feature("http://jabber.org/protocol/pubsub#retrieve-items")
        disco.add_feature("http://jabber.org/protocol/pubsub#persistent-items")

    async def __get_features(self, presence: Presence) -> list[str]:
        from_ = presence.get_from()
        ver_string = presence["caps"]["ver"]
        if ver_string:
            info = await self.xmpp.plugin["xep_0115"].get_caps(from_)
        else:
            info = None
        if info is None:
            async with self.lock(from_):
                iq = await self.xmpp.plugin["xep_0030"].get_info(from_)
            info = iq["disco_info"]
        return info["features"]

    async def on_presence_available(
        self, p: Presence, contact: Optional["LegacyContact"]
    ):
        if p.get_plugin("muc_join", check=True) is not None:
            log.debug("Ignoring MUC presence here")
            return

        to = p.get_to()
        if to != self.xmpp.boundjid.bare:
            # we don't want to push anything for contacts that are not in the user's roster
            if contact is None or not contact.is_friend:
                return

        from_ = p.get_from()
        features = await self.__get_features(p)

        if AvatarMetadata.namespace + "+notify" in features:
            try:
                pep_avatar = await self._get_authorized_avatar(p, contact)
            except XMPPError:
                pass
            else:
                if pep_avatar.metadata is not None:
                    await self.__broadcast(
                        data=pep_avatar.metadata,
                        from_=p.get_to().bare,
                        to=from_,
                        id=pep_avatar.metadata["info"]["id"],
                    )
        if UserNick.namespace + "+notify" in features:
            try:
                pep_nick = await self._get_authorized_nick(p, contact)
            except XMPPError:
                pass
            else:
                await self.__broadcast(data=pep_nick.nick, from_=p.get_to(), to=from_)

        if contact is not None and VCARD4_NAMESPACE + "+notify" in features:
            await self.broadcast_vcard_event(
                p.get_to(), from_, await contact.get_vcard()
            )

    async def broadcast_vcard_event(self, from_: JID, to: JID, vcard: VCard4 | None):
        item = Item()
        item.namespace = VCARD4_NAMESPACE
        item["id"] = "current"
        # vcard: VCard4 = await self.xmpp["xep_0292_provider"].get_vcard(from_, to)
        # The vcard content should NOT be in this event according to the spec:
        # https://xmpp.org/extensions/xep-0292.html#sect-idm45669698174224
        # but movim expects it to be here, and I guess it does not hurt

        log.debug("Broadcast vcard4 event: %s", vcard)
        await self.__broadcast(
            data=vcard,
            from_=JID(from_).bare,
            to=to,
            id="current",
            node=VCARD4_NAMESPACE,
        )

    async def __get_contact(self, stanza: Union[Iq, Presence]):
        session = self.xmpp.get_session_from_stanza(stanza)
        return await session.contacts.by_jid(stanza.get_to())

    async def _get_authorized_avatar(
        self, stanza: Union[Iq, Presence], contact: Optional["LegacyContact"] = None
    ) -> PepAvatar:
        if stanza.get_to() == self.xmpp.boundjid.bare:
            item = PepAvatar()
            item.set_avatar_from_cache(avatar_cache.get_by_pk(self.xmpp.avatar_pk))
            return item

        if contact is None:
            contact = await self.__get_contact(stanza)

        item = PepAvatar()
        if contact.avatar_pk is not None:
            stored = avatar_cache.get_by_pk(contact.avatar_pk)
            assert stored is not None
            item.set_avatar_from_cache(stored)
        return item

    async def _get_authorized_nick(
        self, stanza: Union[Iq, Presence], contact: Optional["LegacyContact"] = None
    ) -> PepNick:
        if stanza.get_to() == self.xmpp.boundjid.bare:
            return PepNick(self.xmpp.COMPONENT_NAME)

        if contact is None:
            contact = await self.__get_contact(stanza)

        if contact.name is not None:
            return PepNick(contact.name)
        else:
            return PepNick()

    def __reply_with(
        self, iq: Iq, content: AvatarData | AvatarMetadata | None, item_id: str | None
    ) -> None:
        requested_items = iq["pubsub"]["items"]

        if len(requested_items) == 0:
            self._reply_with_payload(iq, content, item_id)
        else:
            for item in requested_items:
                if item["id"] == item_id:
                    self._reply_with_payload(iq, content, item_id)
                    return
            else:
                raise XMPPError("item-not-found")

    async def _get_avatar_data(self, iq: Iq):
        pep_avatar = await self._get_authorized_avatar(iq)
        self.__reply_with(iq, pep_avatar.data, pep_avatar.id)

    async def _get_avatar_metadata(self, iq: Iq):
        pep_avatar = await self._get_authorized_avatar(iq)
        self.__reply_with(iq, pep_avatar.metadata, pep_avatar.id)

    async def _get_vcard(self, iq: Iq):
        # this is not the proper way that clients should retrieve VCards, but
        # gajim does it this way.
        # https://xmpp.org/extensions/xep-0292.html#sect-idm45669698174224
        session = self.xmpp.get_session_from_stanza(iq)
        contact = await session.contacts.by_jid(iq.get_to())
        vcard = await contact.get_vcard()
        if vcard is None:
            raise XMPPError("item-not-found")
        self._reply_with_payload(iq, vcard, "current", VCARD4_NAMESPACE)

    @staticmethod
    def _reply_with_payload(
        iq: Iq,
        payload: Optional[Union[AvatarMetadata, AvatarData, VCard4]],
        id_: Optional[str],
        namespace: Optional[str] = None,
    ):
        result = iq.reply()
        item = Item()
        if payload:
            item.set_payload(payload.xml)
            item["id"] = id_
            result["pubsub"]["items"]["node"] = (
                namespace if namespace else payload.namespace
            )
        result["pubsub"]["items"].append(item)
        result.send()

    async def __broadcast(self, data, from_: JidStr, to: OptJidStr = None, **kwargs):
        from_ = JID(from_)
        if from_ != self.xmpp.boundjid.bare and to is not None:
            to = JID(to)
            session = self.xmpp.get_session_from_jid(to)
            if session is None:
                return
            await session.ready

        item = EventItem()
        if data:
            item.set_payload(data.xml)
        for k, v in kwargs.items():
            item[k] = v

        items = EventItems()
        items.append(item)
        items["node"] = kwargs.get("node") or data.namespace

        event = Event()
        event.append(items)

        msg = self.xmpp.Message()
        msg.set_type("headline")
        msg.set_from(from_)
        msg.append(event)

        if to is None:
            for u in self.xmpp.store.users.get_all():
                new_msg = copy(msg)
                new_msg.set_to(u.jid.bare)
                new_msg.send()
        else:
            msg.set_to(to)
            msg.send()

    async def broadcast_avatar(
        self, from_: JidStr, to: JidStr, cached_avatar: Optional[CachedAvatar]
    ) -> None:
        if cached_avatar is None:
            await self.__broadcast(AvatarMetadata(), from_, to)
        else:
            pep_avatar = PepAvatar()
            pep_avatar.set_avatar_from_cache(cached_avatar)
            assert pep_avatar.metadata is not None
            await self.__broadcast(
                pep_avatar.metadata, from_, to, id=pep_avatar.metadata["info"]["id"]
            )

    def broadcast_nick(
        self,
        user_jid: JID,
        jid: JidStr,
        nick: Optional[str] = None,
    ):
        jid = JID(jid)
        nickname = PepNick(nick)
        log.debug("New nickname: %s", nickname.nick)
        self.xmpp.loop.create_task(self.__broadcast(nickname.nick, jid, user_jid.bare))


log = logging.getLogger(__name__)
register_plugin(PubSubComponent)
