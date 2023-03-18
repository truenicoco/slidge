import asyncio
import hashlib
import io
import logging
from copy import copy
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

import aiohttp
from PIL import Image, UnidentifiedImageError
from slixmpp import JID, CoroutineCallback, Iq, Presence, StanzaPath
from slixmpp.plugins.base import BasePlugin, register_plugin
from slixmpp.plugins.xep_0060.stanza import Event, EventItem, EventItems, Item
from slixmpp.plugins.xep_0084 import Data as AvatarData
from slixmpp.plugins.xep_0084 import MetaData as AvatarMetadata
from slixmpp.plugins.xep_0172 import UserNick
from slixmpp.types import JidStr, OptJidStr

from ..util.db import user_store
from ..util.error import XMPPError
from ..util.types import AvatarType, PepItemType
from ..util.xep_0292.stanza import VCard4
from .cache import CachedAvatar, avatar_cache
from .contact import LegacyContact

if TYPE_CHECKING:
    from slidge import BaseGateway

VCARD4_NAMESPACE = "urn:xmpp:vcard4"


class PepItem:
    def __init__(self, authorized_jids: Optional[set[JidStr]] = None):
        self.authorized_jids = authorized_jids


class PepAvatar(PepItem):
    def __init__(self, authorized_jids: Optional[set[JidStr]] = None):
        super().__init__(authorized_jids)
        self.metadata: Optional[AvatarMetadata] = None
        self.id: Optional[str] = None
        self._avatar_data_path: Optional[Path] = None
        self._cache_dir = avatar_cache.dir

    @property
    def data(self) -> Optional[AvatarData]:
        if self._avatar_data_path is None:
            return None
        data = AvatarData()
        data.set_value(self._avatar_data_path.read_bytes())
        return data

    @staticmethod
    def _sha(b: bytes):
        return hashlib.sha1(b).hexdigest()

    def _get_weak_unique_id(self, avatar: AvatarType):
        if isinstance(avatar, str):
            return avatar  # url
        elif isinstance(avatar, bytes):
            return self._sha(avatar)
        elif isinstance(avatar, Path):
            return self._sha(avatar.read_bytes())

    @staticmethod
    async def _get_image(avatar: AvatarType):
        if isinstance(avatar, str):
            async with aiohttp.ClientSession() as session:
                async with session.get(avatar) as response:
                    return Image.open(io.BytesIO(await response.read()))
        elif isinstance(avatar, bytes):
            return Image.open(io.BytesIO(avatar))
        elif isinstance(avatar, Path):
            return Image.open(avatar)
        else:
            raise TypeError("Avatar must be bytes, a Path or a str (URL)", avatar)

    async def set_avatar(
        self, avatar: AvatarType, unique_id: Optional[Union[int, str]] = None
    ):
        if not unique_id:
            if isinstance(avatar, str):
                await self._set_avatar_from_url_alone(avatar)
                return
            unique_id = self._get_weak_unique_id(avatar)

        await self._set_avatar_from_unique_id(avatar, str(unique_id))

    async def _set_avatar_from_unique_id(self, avatar: AvatarType, unique_id: str):
        cached_avatar = avatar_cache.get(unique_id)
        if not cached_avatar:
            img = await self._get_image(avatar)
            cached_avatar = avatar_cache.convert_and_store(img, unique_id)

        await self._set_avatar_from_cache(cached_avatar)

    async def _set_avatar_from_url_alone(self, url: str):
        cached_avatar = await avatar_cache.get_avatar_from_url_alone(url)
        await self._set_avatar_from_cache(cached_avatar)

    async def _set_avatar_from_cache(self, cached_avatar: CachedAvatar):
        metadata = AvatarMetadata()
        self.id = cached_avatar.hash
        metadata.add_info(
            id=cached_avatar.hash,
            itype="image/png",
            ibytes=len(cached_avatar.data),
            height=str(cached_avatar.height),
            width=str(cached_avatar.width),
        )
        self.metadata = metadata
        self._avatar_data_path = cached_avatar.path


class PepNick(PepItem):
    def __init__(
        self, authorized_jids: Optional[set[JidStr]] = None, nick: Optional[str] = None
    ):
        super().__init__(authorized_jids)
        nickname = UserNick()
        if nick is not None:
            nickname["nick"] = nick
        self.nick = nickname


class PubSubComponent(BasePlugin):
    xmpp: "BaseGateway"

    name = "pubsub"
    description = "Pubsub component"
    dependencies = {
        "xep_0030",
        "xep_0060",
        # "xep_0084",
        "xep_0115",
        "xep_0163",
    }
    default_config = {"component_name": None}
    component_name: str

    def __init__(self, *a, **kw):
        super(PubSubComponent, self).__init__(*a, **kw)
        self._avatars = dict[JID, PepAvatar]()
        self._nicks = dict[JID, PepNick]()

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
        self.xmpp.add_event_handler("got_online", self._on_got_online)

        disco = self.xmpp.plugin["xep_0030"]
        disco.add_identity("pubsub", "pep", self.component_name)
        disco.add_identity("account", "registered", self.component_name)
        disco.add_feature("http://jabber.org/protocol/pubsub#event")
        disco.add_feature("http://jabber.org/protocol/pubsub#retrieve-items")
        disco.add_feature("http://jabber.org/protocol/pubsub#persistent-items")

    async def _on_got_online(self, p: Presence):
        from_ = p.get_from()
        ver_string = p["caps"]["ver"]
        info = None

        to = p.get_to()

        # we don't want to push anything for contacts that are not in the user's roster
        if to != self.xmpp.boundjid.bare:
            session = self.xmpp.get_session_from_stanza(p)

            if session is None:
                return

            await session.ready

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
            if not contact.added_to_roster:
                return

        if ver_string:
            await asyncio.sleep(5)
            info = await self.xmpp.plugin["xep_0115"].get_caps(from_)
        if info is None:
            info = await self.xmpp.plugin["xep_0030"].get_info(from_)
        features = info["features"]
        if AvatarMetadata.namespace + "+notify" in features:
            try:
                pep_avatar = self._get_authorized_avatar(p)
            except XMPPError:
                pass
            else:
                await self._broadcast(
                    data=pep_avatar.metadata,
                    from_=p.get_to(),
                    to=from_,
                    id=pep_avatar.metadata["info"]["id"],
                )
        if UserNick.namespace + "+notify" in features:
            try:
                pep_nick = self._get_authorized_nick(p)
            except XMPPError:
                pass
            else:
                await self._broadcast(data=pep_nick.nick, from_=p.get_to(), to=from_)

        if VCARD4_NAMESPACE + "+notify" in features:
            await self.broadcast_vcard_event(p.get_to(), to=from_)

    async def broadcast_vcard_event(self, from_, to):
        item = Item()
        item.namespace = VCARD4_NAMESPACE
        item["id"] = "current"
        vcard: VCard4 = await self.xmpp["xep_0292_provider"].get_vcard(from_, to)
        # The vcard content should NOT be in this event according to the spec:
        # https://xmpp.org/extensions/xep-0292.html#sect-idm45669698174224
        # but movim expects it to be here, and I guess

        log.debug("Broadcast vcard4 event: %s", vcard)
        await self._broadcast(
            data=vcard,
            from_=JID(from_).bare,
            to=to,
            id="current",
            node=VCARD4_NAMESPACE,
        )

    @staticmethod
    def _get_authorized_item(
        store: dict[JID, PepItemType], stanza: Union[Iq, Presence]
    ) -> PepItemType:
        item = store.get(stanza.get_to())
        if item is None:
            raise XMPPError("item-not-found")

        if item.authorized_jids is not None:
            if stanza.get_from().bare not in item.authorized_jids:
                raise XMPPError("item-not-found")

        return item

    def _get_authorized_avatar(self, stanza: Union[Iq, Presence]):
        return self._get_authorized_item(self._avatars, stanza)

    def _get_authorized_nick(self, stanza: Union[Iq, Presence]):
        return self._get_authorized_item(self._nicks, stanza)

    async def _get_avatar_data(self, iq: Iq):
        pep_avatar = self._get_authorized_avatar(iq)

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
        pep_avatar = self._get_authorized_avatar(iq)

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
        vcard: VCard4 = await self.xmpp["xep_0292_provider"].get_vcard(
            iq.get_to().bare, iq.get_from().bare
        )
        log.debug("VCARD: %s -- %s -- %s", iq.get_to().bare, iq.get_from().bare, vcard)
        if vcard is None:
            raise XMPPError("item-not-found")
        self._reply_with_payload(iq, vcard, "current", VCARD4_NAMESPACE)

    def get_avatar(self, jid: JidStr):
        return self._avatars.get(JID(jid))

    @staticmethod
    def _reply_with_payload(
        iq: Iq,
        payload: Union[AvatarMetadata, AvatarData, VCard4],
        id_: str,
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

    async def _broadcast(self, data, from_: JidStr, to: OptJidStr = None, **kwargs):
        from_ = JID(from_)
        if from_ != self.xmpp.boundjid.bare and to is not None:
            to = JID(to)
            session = self.xmpp.get_session_from_jid(to)
            if session is None:
                return
            await session.ready
            entity = await session.get_contact_or_group_or_participant(from_)
            if isinstance(entity, LegacyContact) and not entity.added_to_roster:
                return

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
            for u in user_store.get_all():
                new_msg = copy(msg)
                new_msg.set_to(u.bare_jid)
                new_msg.send()
        else:
            msg.set_to(to)
            msg.send()

    async def set_avatar(
        self,
        jid: JidStr,
        avatar: Optional[AvatarType] = None,
        restrict_to: OptJidStr = None,
        unique_id=None,
    ):
        jid = JID(jid)
        if avatar is None:
            try:
                del self._avatars[jid]
            except KeyError:
                pass
            await self._broadcast(AvatarMetadata(), jid, restrict_to)
        else:
            if restrict_to:
                pep_avatar = PepAvatar({restrict_to})
            else:
                pep_avatar = PepAvatar()
            try:
                await pep_avatar.set_avatar(avatar, unique_id)
            except UnidentifiedImageError as e:
                log.warning("Failed to set avatar for %s: %r", self, e)
                return

            _add_or_extend_allowed_jids(jid, self._avatars, pep_avatar)
            if pep_avatar.metadata is None:
                raise RuntimeError
            await self._broadcast(
                pep_avatar.metadata,
                jid,
                restrict_to,
                id=pep_avatar.metadata["info"]["id"],
            )

    def set_nick(
        self,
        jid: JidStr,
        nick: Optional[str] = None,
        restrict_to: OptJidStr = None,
    ):
        jid = JID(jid)
        if restrict_to:
            nickname = PepNick({restrict_to}, nick)
        else:
            nickname = PepNick(None, nick)
        _add_or_extend_allowed_jids(jid, self._nicks, nickname)
        log.debug("New nickname: %s", nickname.nick)
        self.xmpp.loop.create_task(self._broadcast(nickname.nick, jid, restrict_to))


def _add_or_extend_allowed_jids(
    jid: JID, store: dict[JID, PepItemType], item: PepItemType
):
    already_here = store.get(jid)
    if already_here is None:
        store[jid] = item
        return

    before = already_here.authorized_jids
    now = item.authorized_jids

    if before is None and now is None:
        store[jid] = item
        return

    if (before is None and now is not None) or (now is None and before is not None):
        log.warning("Restriction status of %s changed changed. This is a bug.", item)
        store[jid] = item
        return

    assert isinstance(now, set) and isinstance(before, set)
    log.debug("Extending JID restrictions of %s from %s with %s", item, before, now)
    now |= before
    store[jid] = item


log = logging.getLogger(__name__)
register_plugin(PubSubComponent)
