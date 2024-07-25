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
from ..util.types import URL
from .mixins.lock import NamedLockMixin

if TYPE_CHECKING:
    from slidge import BaseGateway

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
        self.xmpp.add_event_handler("presence_available", self._on_presence_available)

        disco = self.xmpp.plugin["xep_0030"]
        disco.add_identity("pubsub", "pep", self.component_name)
        disco.add_identity("account", "registered", self.component_name)
        disco.add_feature("http://jabber.org/protocol/pubsub#event")
        disco.add_feature("http://jabber.org/protocol/pubsub#retrieve-items")
        disco.add_feature("http://jabber.org/protocol/pubsub#persistent-items")

    async def _on_presence_available(self, p: Presence):
        if p.get_plugin("muc_join", check=True) is not None:
            log.debug("Ignoring MUC presence here")
            return

        from_ = p.get_from()
        ver_string = p["caps"]["ver"]
        info = None

        to = p.get_to()

        contact = None
        # we don't want to push anything for contacts that are not in the user's roster
        if to != self.xmpp.boundjid.bare:
            session = self.xmpp.get_session_from_stanza(p)

            if session is None:
                return

            await session.contacts.ready
            try:
                contact = await session.contacts.by_jid(to)
            except XMPPError as e:
                log.debug(
                    "Could not determine if %s was added to the roster: %s", to, e
                )
                return
            except Exception as e:
                log.warning("Could not determine if %s was added to the roster.", to)
                log.exception(e)
                return
            if not contact.is_friend:
                return

        if ver_string:
            info = await self.xmpp.plugin["xep_0115"].get_caps(from_)
        if info is None:
            async with self.lock(from_):
                iq = await self.xmpp.plugin["xep_0030"].get_info(from_)
            info = iq["disco_info"]
        features = info["features"]
        if AvatarMetadata.namespace + "+notify" in features:
            try:
                pep_avatar = await self._get_authorized_avatar(p)
            except XMPPError:
                pass
            else:
                if pep_avatar.metadata is not None:
                    await self.__broadcast(
                        data=pep_avatar.metadata,
                        from_=p.get_to(),
                        to=from_,
                        id=pep_avatar.metadata["info"]["id"],
                    )
        if UserNick.namespace + "+notify" in features:
            try:
                pep_nick = await self._get_authorized_nick(p)
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

    async def _get_authorized_avatar(self, stanza: Union[Iq, Presence]) -> PepAvatar:
        if stanza.get_to() == self.xmpp.boundjid.bare:
            item = PepAvatar()
            item.set_avatar_from_cache(avatar_cache.get_by_pk(self.xmpp.avatar_pk))
            return item

        session = self.xmpp.get_session_from_stanza(stanza)
        entity = await session.get_contact_or_group_or_participant(stanza.get_to())

        item = PepAvatar()
        avatar_id = entity.avatar_id
        if avatar_id is not None:
            stored = avatar_cache.get(
                avatar_id if isinstance(avatar_id, URL) else str(avatar_id)
            )
            assert stored is not None
            item.set_avatar_from_cache(stored)
        return item

    async def _get_authorized_nick(self, stanza: Union[Iq, Presence]) -> PepNick:
        if stanza.get_to() == self.xmpp.boundjid.bare:
            return PepNick(self.xmpp.COMPONENT_NAME)

        session = self.xmpp.get_session_from_stanza(stanza)
        entity = await session.contacts.by_jid(stanza.get_to())

        if entity.name is not None:
            return PepNick(entity.name)
        else:
            return PepNick()

    async def _get_avatar_data(self, iq: Iq):
        pep_avatar = await self._get_authorized_avatar(iq)

        requested_items = iq["pubsub"]["items"]
        if len(requested_items) == 0:
            self._reply_with_payload(iq, pep_avatar.data, pep_avatar.id)
        else:
            for item in requested_items:
                if item["id"] == pep_avatar.id:
                    self._reply_with_payload(iq, pep_avatar.data, pep_avatar.id)
                    return
            else:
                raise XMPPError("item-not-found")

    async def _get_avatar_metadata(self, iq: Iq):
        pep_avatar = await self._get_authorized_avatar(iq)

        requested_items = iq["pubsub"]["items"]
        if len(requested_items) == 0:
            self._reply_with_payload(iq, pep_avatar.metadata, pep_avatar.id)
        else:
            for item in requested_items:
                if item["id"] == pep_avatar.id:
                    self._reply_with_payload(iq, pep_avatar.metadata, pep_avatar.id)
                    return
            else:
                raise XMPPError("item-not-found")

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
