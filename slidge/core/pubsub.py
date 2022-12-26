import asyncio
import hashlib
import io
import logging
from copy import copy
from pathlib import Path
from typing import Optional, Union

from PIL import Image, UnidentifiedImageError
from slixmpp import JID, ComponentXMPP, CoroutineCallback, Iq, Presence, StanzaPath
from slixmpp.exceptions import XMPPError
from slixmpp.plugins.base import BasePlugin, register_plugin
from slixmpp.plugins.xep_0060.stanza import Event, EventItem, EventItems, Item
from slixmpp.plugins.xep_0084 import Data as AvatarData
from slixmpp.plugins.xep_0084 import MetaData as AvatarMetadata
from slixmpp.plugins.xep_0172 import UserNick
from slixmpp.types import JidStr, OptJidStr

from ..util.db import user_store
from ..util.types import AvatarType, PepItemType
from ..util.xep_0292.stanza import VCard4
from . import config
from .cache import avatar_cache

VCARD4_NAMESPACE = "urn:xmpp:vcard4"


class PepItem:
    def __init__(self, authorized_jid: Optional[JidStr] = None):
        self.authorized_jid = authorized_jid


class PepAvatar(PepItem):
    def __init__(self, authorized_jid: Optional[JidStr] = None):
        super().__init__(authorized_jid)
        self.metadata: Optional[AvatarMetadata] = None
        self.data: Optional[AvatarData] = None
        self.id: Optional[str] = None

    async def set_avatar(self, avatar: AvatarType):
        if isinstance(avatar, str):
            return await self.set_avatar_from_url(avatar)
        elif isinstance(avatar, bytes):
            img = Image.open(io.BytesIO(avatar))
        elif isinstance(avatar, Path):
            img = Image.open(avatar)
        else:
            raise TypeError("Avatar must be bytes, a Path or a str (URL)", avatar)

        metadata = AvatarMetadata()

        resampled = False
        if (size := config.AVATAR_SIZE) and any(x > size for x in img.size):
            img.thumbnail((size, size))
            log.debug("Resampled image to %s", img.size)
            resampled = True

        if not resampled and img.format == "PNG" and isinstance(avatar, bytes):
            avatar_bytes = avatar
        elif not resampled and img.format == "PNG" and isinstance(avatar, Path):
            with avatar.open("rb") as f:
                avatar_bytes = f.read()
        else:
            with io.BytesIO() as f:
                img.save(f, format="PNG")
                avatar_bytes = f.getvalue()

        hash_ = hashlib.sha1(avatar_bytes).hexdigest()
        self.id = hash_
        metadata.add_info(
            id=hash_,
            itype="image/png",
            ibytes=len(avatar_bytes),
            height=str(img.height),
            width=str(img.width),
        )
        self.metadata = metadata

        data = AvatarData()
        data.set_value(avatar_bytes)
        self.data = data

    async def set_avatar_from_url(self, url: str):
        avatar = await avatar_cache.get_avatar(url)
        metadata = AvatarMetadata()
        self.id = avatar.hash
        metadata.add_info(
            id=avatar.hash,
            itype="image/png",
            ibytes=len(avatar.data),
            height=str(avatar.height),
            width=str(avatar.width),
        )
        self.metadata = metadata

        data = AvatarData()
        data.set_value(avatar.data)
        self.data = data


class PepNick(PepItem):
    def __init__(
        self, authorized_jid: Optional[JidStr] = None, nick: Optional[str] = None
    ):
        super().__init__(authorized_jid)
        nickname = UserNick()
        if nick is not None:
            nickname["nick"] = nick
        self.nick = nickname


class PubSubComponent(BasePlugin):
    xmpp: ComponentXMPP

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
                self._broadcast(
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
                self._broadcast(data=pep_nick.nick, from_=p.get_to(), to=from_)

        if VCARD4_NAMESPACE + "+notify" in features:
            self.broadcast_vcard_event(p.get_to(), to=from_)

    def broadcast_vcard_event(self, from_, to):
        item = Item()
        item.namespace = VCARD4_NAMESPACE
        item["id"] = "current"
        vcard: VCard4 = self.xmpp["xep_0292_provider"].get_vcard(from_, to)
        # The vcard content should NOT be in this event according to the spec:
        # https://xmpp.org/extensions/xep-0292.html#sect-idm45669698174224
        # but movim expects it to be here, and I guess

        log.debug("Broadcast vcard4 event: %s", vcard)
        self._broadcast(
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

        if item.authorized_jid is not None:
            if stanza.get_from().bare != item.authorized_jid:
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
        vcard: VCard4 = self.xmpp["xep_0292_provider"].get_vcard(
            iq.get_to().bare, iq.get_from().bare
        )
        log.debug("VCARD: %s -- %s -- %s", iq.get_to().bare, iq.get_from().bare, vcard)
        if vcard is None:
            raise XMPPError("item-not-found")
        self._reply_with_payload(iq, vcard, "current", VCARD4_NAMESPACE)

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

    def _broadcast(self, data, from_: JidStr, to: OptJidStr = None, **kwargs):
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
    ):
        jid = JID(jid)
        if avatar is None:
            try:
                del self._avatars[jid]
            except KeyError:
                pass
            self._broadcast(AvatarMetadata(), jid, restrict_to)
        else:
            pep_avatar = PepAvatar()
            try:
                await pep_avatar.set_avatar(avatar)
            except UnidentifiedImageError as e:
                log.warning("Failed to set avatar for %s: %r", self, e)
                return

            pep_avatar.authorized_jid = restrict_to
            self._avatars[jid] = pep_avatar
            if pep_avatar.metadata is None:
                raise RuntimeError
            self._broadcast(
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
        nickname = PepNick(restrict_to, nick)
        self._nicks[jid] = nickname
        log.debug("NICK: %s", nickname.nick)
        self._broadcast(nickname.nick, jid, restrict_to)


log = logging.getLogger(__name__)
register_plugin(PubSubComponent)
