import hashlib
import io
import logging
from copy import copy
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Type, Union

from PIL import Image, UnidentifiedImageError
from PIL.Image import Image as PILImage
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

from ..contact.contact import LegacyContact
from ..contact.roster import ContactIsUser
from ..util.db import GatewayUser, user_store
from ..util.sql import db
from ..util.types import AvatarType, LegacyFileIdType, PepItemType
from .cache import CachedAvatar, avatar_cache
from .mixins.lock import NamedLockMixin

if TYPE_CHECKING:
    from slidge import BaseGateway

VCARD4_NAMESPACE = "urn:xmpp:vcard4"


class PepItem:
    @staticmethod
    def from_db(jid: JID, user: Optional[GatewayUser] = None) -> Optional["PepItem"]:
        raise NotImplementedError

    def to_db(self, jid: JID, user: Optional[GatewayUser] = None):
        raise NotImplementedError


class PepAvatar(PepItem):
    def __init__(self, jid: JID):
        self.metadata: Optional[AvatarMetadata] = None
        self.id: Optional[str] = None
        self.jid = jid
        self._avatar_data_path: Optional[Path] = None

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
    async def _get_image(avatar: AvatarType) -> PILImage:
        if isinstance(avatar, str):
            # async with aiohttp.ClientSession() as session:
            async with avatar_cache.http.get(avatar) as response:
                return Image.open(io.BytesIO(await response.read()))
        elif isinstance(avatar, bytes):
            return Image.open(io.BytesIO(avatar))
        elif isinstance(avatar, Path):
            return Image.open(avatar)
        else:
            raise TypeError("Avatar must be bytes, a Path or a str (URL)", avatar)

    async def set_avatar(
        self, avatar: AvatarType, unique_id: Optional[LegacyFileIdType] = None
    ):
        if unique_id is None:
            if isinstance(avatar, str):
                await self._set_avatar_from_url_alone(avatar)
                return
            unique_id = self._get_weak_unique_id(avatar)

        await self._set_avatar_from_unique_id(avatar, unique_id)

    async def _set_avatar_from_unique_id(
        self, avatar: AvatarType, unique_id: LegacyFileIdType
    ):
        cached_avatar = avatar_cache.get(unique_id)
        if cached_avatar:
            # this shouldn't be necessary but here to re-use avatars downloaded
            # before the change introducing the JID to unique ID mapping
            avatar_cache.store_jid(self.jid, unique_id)
        else:
            img = await self._get_image(avatar)
            cached_avatar = await avatar_cache.convert_and_store(
                img, unique_id, self.jid
            )

        self._set_avatar_from_cache(cached_avatar)

    async def _set_avatar_from_url_alone(self, url: str):
        cached_avatar = await avatar_cache.get_avatar_from_url_alone(url, self.jid)
        self._set_avatar_from_cache(cached_avatar)

    def _set_avatar_from_cache(self, cached_avatar: CachedAvatar):
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

    @staticmethod
    def from_db(jid: JID, user: Optional[GatewayUser] = None) -> Optional["PepAvatar"]:
        cached_id = db.avatar_get(jid)
        if cached_id is None:
            return None
        item = PepAvatar(jid)
        cached_avatar = avatar_cache.get(cached_id)
        if cached_avatar is None:
            raise XMPPError("internal-server-error")
        item._set_avatar_from_cache(cached_avatar)
        return item

    def to_db(self, jid: JID, user=None):
        cached_id = avatar_cache.get_cached_id_for(jid)
        if cached_id is None:
            log.warning("Could not store avatar for %s", jid)
            return
        db.avatar_store(jid, cached_id)

    @staticmethod
    def remove_from_db(jid: JID):
        db.avatar_delete(jid)


class PepNick(PepItem):
    def __init__(self, nick: Optional[str] = None):
        nickname = UserNick()
        if nick is not None:
            nickname["nick"] = nick
        self.nick = nickname
        self.__nick_str = nick

    @staticmethod
    def from_db(jid: JID, user: Optional[GatewayUser] = None) -> Optional["PepNick"]:
        if user is None:
            raise XMPPError("not-allowed")
        nick = db.nick_get(jid, user)
        if nick is None:
            return None
        return PepNick(nick)

    def to_db(self, jid: JID, user: Optional[GatewayUser] = None):
        if user is None:
            raise XMPPError("not-allowed")
        db.nick_store(jid, str(self.__nick_str), user)


class PubSubComponent(NamedLockMixin, BasePlugin):
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
                if pep_avatar.metadata is None:
                    raise XMPPError("internal-server-error", "Avatar but no metadata?")
                await self._broadcast(
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

    async def _get_authorized_item(
        self, cls: Type[PepItemType], stanza: Union[Iq, Presence]
    ) -> PepItemType:
        sto = stanza.get_to()
        user = user_store.get_by_jid(stanza.get_from())
        item = cls.from_db(sto, user)
        if item is None:
            raise XMPPError("item-not-found")

        if sto != self.xmpp.boundjid.bare:
            session = self.xmpp.get_session_from_stanza(stanza)
            await session.contacts.by_jid(sto)

        return item  # type:ignore

    async def _get_authorized_avatar(self, stanza: Union[Iq, Presence]) -> PepAvatar:
        return await self._get_authorized_item(PepAvatar, stanza)

    async def _get_authorized_nick(self, stanza: Union[Iq, Presence]) -> PepNick:
        return await self._get_authorized_item(PepNick, stanza)

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
        vcard: VCard4 = await self.xmpp["xep_0292_provider"].get_vcard(
            iq.get_to().bare, iq.get_from().bare
        )
        log.debug("VCARD: %s -- %s -- %s", iq.get_to().bare, iq.get_from().bare, vcard)
        if vcard is None:
            raise XMPPError("item-not-found")
        self._reply_with_payload(iq, vcard, "current", VCARD4_NAMESPACE)

    @staticmethod
    def get_avatar(jid: JID):
        return PepAvatar.from_db(jid)

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

    async def _broadcast(self, data, from_: JidStr, to: OptJidStr = None, **kwargs):
        from_ = JID(from_)
        if from_ != self.xmpp.boundjid.bare and to is not None:
            to = JID(to)
            session = self.xmpp.get_session_from_jid(to)
            if session is None:
                return
            await session.ready
            try:
                entity = await session.get_contact_or_group_or_participant(from_)
            except ContactIsUser:
                return
            if isinstance(entity, LegacyContact) and not entity.is_friend:
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
        broadcast_to: OptJidStr = None,
        unique_id=None,
        broadcast=True,
    ):
        jid = JID(jid)
        if avatar is None:
            PepAvatar.remove_from_db(jid)
            await self._broadcast(AvatarMetadata(), jid, broadcast_to)
            avatar_cache.delete_jid(jid)
        else:
            pep_avatar = PepAvatar(jid)
            try:
                await pep_avatar.set_avatar(avatar, unique_id)
            except (UnidentifiedImageError, FileNotFoundError) as e:
                log.warning("Failed to set avatar for %s: %r", self, e)
                return
            pep_avatar.to_db(jid)
            if pep_avatar.metadata is None:
                raise RuntimeError
            if not broadcast:
                return
            await self._broadcast(
                pep_avatar.metadata,
                jid,
                broadcast_to,
                id=pep_avatar.metadata["info"]["id"],
            )

    async def set_avatar_from_cache(
        self, jid: JID, send_empty: bool, broadcast_to: OptJidStr = None, broadcast=True
    ):
        uid = avatar_cache.get_cached_id_for(jid)
        if uid is None:
            if not send_empty:
                return
            self.xmpp.loop.create_task(
                self.set_avatar(jid, None, broadcast_to, uid, broadcast)
            )
            return
        cached_avatar = avatar_cache.get(str(uid))
        if cached_avatar is None:
            # should not happen but well…
            log.warning(
                "Something is wrong with the avatar, %s won't have an "
                "avatar because avatar not found in cache",
                jid,
            )
            return
        self.xmpp.loop.create_task(
            self.set_avatar(jid, cached_avatar.path, broadcast_to, uid, broadcast)
        )

    def set_nick(
        self,
        user: GatewayUser,
        jid: JidStr,
        nick: Optional[str] = None,
    ):
        jid = JID(jid)
        nickname = PepNick(nick)
        nickname.to_db(jid, user)
        log.debug("New nickname: %s", nickname.nick)
        self.xmpp.loop.create_task(self._broadcast(nickname.nick, jid, user.bare_jid))

    async def broadcast_all(self, from_: JID, to: JID):
        """
        Force push avatar and nick for a stored JID.
        """
        a = PepAvatar.from_db(from_)
        if a:
            if a.metadata:
                await self._broadcast(
                    a.metadata, from_, to, id=a.metadata["info"]["id"]
                )
            else:
                log.warning("No metadata associated to this cached avatar?!")
        n = PepNick.from_db(from_, user_store.get_by_jid(to))
        if n:
            await self._broadcast(n.nick, from_, to)


log = logging.getLogger(__name__)
register_plugin(PubSubComponent)
