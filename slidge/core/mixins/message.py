import logging
import os
import shutil
import stat
import tempfile
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Iterable, Optional, Union
from uuid import uuid4

from slixmpp import JID, Message
from slixmpp.exceptions import IqError
from slixmpp.plugins.xep_0363 import FileUploadError
from slixmpp.types import MessageTypes

from slidge.core import config
from slidge.util.types import LegacyMessageType, LegacyThreadType

from ...util import BiDict
from ...util.types import ChatState, Marker, ProcessingHint
from ...util.util import fix_suffix, remove_emoji_variation_selector_16
from ...util.xep_0385.stanza import Sims
from ...util.xep_0447.stanza import StatelessFileSharing
from .base import BaseSender


class MessageMaker(BaseSender):
    mtype: MessageTypes = NotImplemented
    STRIP_SHORT_DELAY = False
    USE_STANZA_ID = False

    def _make_message(
        self,
        state: Optional[ChatState] = None,
        hints: Iterable[ProcessingHint] = (),
        legacy_msg_id: Optional[LegacyMessageType] = None,
        when: Optional[datetime] = None,
        reply_to_msg_id: Optional[LegacyMessageType] = None,
        reply_to_fallback_text: Optional[str] = None,
        reply_to_jid: Optional[JID] = None,
        carbon=False,
        **kwargs,
    ):
        body = kwargs.pop("mbody", None)
        mfrom = kwargs.pop("mfrom", self.jid)
        mto = kwargs.pop("mto", None)
        thread = kwargs.pop("thread", None)
        if carbon:
            # the msg needs to have jabber:client as xmlns, so
            # we don't want to associate with the XML stream
            msg_cls = Message
        else:
            msg_cls = self.xmpp.Message  # type:ignore
        msg = msg_cls(sfrom=mfrom, stype=self.mtype, sto=mto, **kwargs)
        if body:
            msg["body"] = body
            state = "active"
        if thread:
            known_threads = self.session.threads.inverse  # type:ignore
            msg["thread"] = known_threads.get(thread) or str(thread)
        if state:
            msg["chat_state"] = state
        for hint in hints:
            msg.enable(hint)
        self._set_msg_id(msg, legacy_msg_id)
        self._add_delay(msg, when)
        self._add_reply_to(msg, reply_to_msg_id, reply_to_fallback_text, reply_to_jid)
        return msg

    def _set_msg_id(
        self, msg: Message, legacy_msg_id: Optional[LegacyMessageType] = None
    ):
        if legacy_msg_id is not None:
            i = self._legacy_to_xmpp(legacy_msg_id)
            msg.set_id(i)
            if self.USE_STANZA_ID:
                msg["stanza_id"]["id"] = i
                msg["stanza_id"]["by"] = self.muc.jid  # type: ignore
        elif self.USE_STANZA_ID:
            msg["stanza_id"]["id"] = str(uuid4())
            msg["stanza_id"]["by"] = self.muc.jid  # type: ignore

    def _legacy_to_xmpp(self, legacy_id: LegacyMessageType):
        return self.session.sent.get(
            legacy_id
        ) or self.session.legacy_msg_id_to_xmpp_msg_id(legacy_id)

    def _add_delay(self, msg: Message, when: Optional[datetime]):
        if when:
            if when.tzinfo is None:
                when = when.astimezone(timezone.utc)
            if self.STRIP_SHORT_DELAY:
                delay = datetime.now().astimezone(timezone.utc) - when
                if delay < config.IGNORE_DELAY_THRESHOLD:
                    return
            msg["delay"].set_stamp(when)
            msg["delay"].set_from(self.xmpp.boundjid.bare)

    def _add_reply_to(
        self,
        msg: Message,
        reply_to_msg_id: Optional[LegacyMessageType] = None,
        reply_to_fallback_text: Optional[str] = None,
        reply_to_author: Optional[JID] = None,
    ):
        if reply_to_msg_id is not None:
            xmpp_id = self._legacy_to_xmpp(reply_to_msg_id)
            msg["reply"]["id"] = xmpp_id
            if reply_to_author:
                msg["reply"]["to"] = reply_to_author
            if reply_to_fallback_text:
                msg["feature_fallback"].add_quoted_fallback(reply_to_fallback_text)


class ChatStateMixin(MessageMaker):
    def _chat_state(self, state: ChatState, **kwargs):
        msg = self._make_message(
            state=state, hints={"no-store"}, carbon=kwargs.get("carbon")
        )
        self._send(msg, **kwargs)

    def active(self, **kwargs):
        """
        Send an "active" chat state (:xep:`0085`) from this contact to the user.
        """
        self._chat_state("active", **kwargs)

    def composing(self, **kwargs):
        """
        Send a "composing" (ie "typing notification") chat state (:xep:`0085`) from this contact to the user.
        """
        self._chat_state("composing", **kwargs)

    def paused(self, **kwargs):
        """
        Send a "paused" (ie "typing paused notification") chat state (:xep:`0085`) from this contact to the user.
        """
        self._chat_state("paused", **kwargs)

    def inactive(self, **kwargs):
        """
        Send an "inactive" (ie "typing paused notification") chat state (:xep:`0085`) from this contact to the user.
        """
        self._chat_state("inactive", **kwargs)

    def gone(self, **kwargs):
        """
        Send an "inactive" (ie "typing paused notification") chat state (:xep:`0085`) from this contact to the user.
        """
        self._chat_state("gone", **kwargs)


class MarkerMixin(MessageMaker):
    is_group: bool = NotImplemented

    def _make_marker(
        self, legacy_msg_id: LegacyMessageType, marker: Marker, carbon=False
    ):
        msg = self._make_message(carbon=carbon)
        msg[marker]["id"] = self._legacy_to_xmpp(legacy_msg_id)
        return msg

    def ack(self, legacy_msg_id: LegacyMessageType, **kwargs):
        """
        Send an "acknowledged" message marker (:xep:`0333`) from this contact to the user.

        :param legacy_msg_id: The message this marker refers to
        """
        self._send(
            self._make_marker(
                legacy_msg_id, "acknowledged", carbon=kwargs.get("carbon")
            ),
            **kwargs,
        )

    def received(self, legacy_msg_id: LegacyMessageType, **kwargs):
        """
        Send a "received" message marker (:xep:`0333`) from this contact to the user

        :param legacy_msg_id: The message this marker refers to
        """
        carbon = kwargs.get("carbon")
        self._send(
            self._make_marker(legacy_msg_id, "received", carbon=carbon), **kwargs
        )

    def displayed(self, legacy_msg_id: LegacyMessageType, **kwargs):
        """
        Send a "displayed" message marker (:xep:`0333`) from this contact to the user.

        :param legacy_msg_id: The message this marker refers to
        """
        self._send(
            self._make_marker(legacy_msg_id, "displayed", carbon=kwargs.get("carbon")),
            **kwargs,
        )


class AttachmentMixin(MessageMaker):
    __legacy_file_ids_to_urls = BiDict[Union[str, int], str]()
    __uploaded_urls_to_sims = dict[Union[str, int], Sims]()
    __uploaded_urls_to_sfs = dict[Union[str, int], StatelessFileSharing]()

    def send_text(self, *_, **k):
        raise NotImplementedError

    async def __upload(
        self,
        file_path: Path,
        file_name: Optional[str] = None,
        content_type: Optional[str] = None,
    ):
        if file_name and file_path.name != file_name:
            d = Path(tempfile.mkdtemp())
            temp = d / file_name
            temp.symlink_to(file_path)
            file_path = temp
        else:
            d = None
        try:
            new_url = await self.xmpp.plugin["xep_0363"].upload_file(
                filename=file_path,
                content_type=content_type,
                ifrom=config.UPLOAD_REQUESTER or self.xmpp.boundjid,
            )
        except (FileUploadError, IqError) as e:
            warnings.warn(f"Something is wrong with the upload service: {e}")
            return None
        finally:
            if d is not None:
                file_path.unlink()
                d.rmdir()

        return new_url

    @staticmethod
    async def __no_upload(
        file_path: Path,
        file_name: Optional[str] = None,
        legacy_file_id: Optional[Union[str, int]] = None,
    ):
        file_id = str(uuid4()) if legacy_file_id is None else str(legacy_file_id)
        assert config.NO_UPLOAD_PATH is not None
        assert config.NO_UPLOAD_URL_PREFIX is not None
        destination_dir = Path(config.NO_UPLOAD_PATH) / file_id

        if destination_dir.exists():
            log.debug("Dest dir exists: %s", destination_dir)
            files = list(f for f in destination_dir.glob("**/*") if f.is_file())
            if len(files) == 1:
                log.debug(
                    "Found the legacy attachment '%s' at '%s'",
                    legacy_file_id,
                    files[0],
                )
                name = files[0].name
                uu = files[0].parent.name  # anti-obvious url trick, see below
                return files[0], "/".join(
                    [config.NO_UPLOAD_URL_PREFIX, file_id, uu, name]
                )
            else:
                log.warning(
                    "There are several or zero files in %s, "
                    "slidge doesn't know which one to pick among %s",
                    destination_dir,
                    files,
                )
                return None, None

        log.debug("Did not find a file in: %s", destination_dir)
        # let's use a UUID to avoid URLs being too obvious
        uu = str(uuid4())
        destination_dir = destination_dir / uu
        destination_dir.mkdir(parents=True)

        name = file_name or file_path.name
        destination = destination_dir / name
        method = config.NO_UPLOAD_METHOD
        if method == "copy":
            shutil.copy(file_path, destination)
        elif method == "hardlink":
            os.link(file_path, destination)
        elif method == "symlink":
            os.symlink(file_path, destination, target_is_directory=True)
        elif method == "move":
            shutil.move(file_path, destination)
        else:
            raise RuntimeError("No upload method not recognized", method)

        if config.NO_UPLOAD_FILE_READ_OTHERS:
            log.debug("Changing perms of %s", destination)
            destination.chmod(destination.stat().st_mode | stat.S_IROTH)

        uploaded_url = "/".join([config.NO_UPLOAD_URL_PREFIX, file_id, uu, name])

        return destination, uploaded_url

    async def __get_url(
        self,
        file_path: Optional[Path] = None,
        data_stream: Optional[IO[bytes]] = None,
        data: Optional[bytes] = None,
        file_url: Optional[str] = None,
        file_name: Optional[str] = None,
        content_type: Optional[str] = None,
        legacy_file_id: Optional[Union[str, int]] = None,
    ):
        if legacy_file_id:
            cache = self.__legacy_file_ids_to_urls.get(legacy_file_id)
            if cache is not None:
                async with self.session.http.head(cache) as r:
                    if r.status < 400:
                        return False, None, cache
                    else:
                        del self.__legacy_file_ids_to_urls[legacy_file_id]

        if file_url and config.USE_ATTACHMENT_ORIGINAL_URLS:
            return False, None, file_url

        if file_path is None:
            file_name = str(uuid4()) if file_name is None else file_name
            temp_dir = Path(tempfile.mkdtemp())
            file_path = temp_dir / file_name
            if file_url:
                async with self.session.http.get(file_url) as r:
                    with file_path.open("wb") as f:
                        f.write(await r.read())

            else:
                if data_stream is not None:
                    data = data_stream.read()
                if data is None:
                    raise RuntimeError

                with file_path.open("wb") as f:
                    f.write(data)

            is_temp = not bool(config.NO_UPLOAD_PATH)
        else:
            is_temp = False

        if config.FIX_FILENAME_SUFFIX_MIME_TYPE:
            file_name = str(fix_suffix(file_path, content_type, file_name))

        if config.NO_UPLOAD_PATH:
            local_path, new_url = await self.__no_upload(
                file_path, file_name, legacy_file_id
            )
        else:
            local_path = file_path
            new_url = await self.__upload(file_path, file_name, content_type)

        if legacy_file_id and new_url:
            self.__legacy_file_ids_to_urls[legacy_file_id] = new_url

        return is_temp, local_path, new_url

    def __set_sims(
        self,
        msg: Message,
        uploaded_url: str,
        path: Path,
        content_type: Optional[str] = None,
        caption: Optional[str] = None,
    ):
        cache = self.__uploaded_urls_to_sims.get(uploaded_url)
        if cache:
            msg.append(cache)
            return

        sims = self.xmpp["xep_0385"].get_sims(
            path, [uploaded_url], content_type, caption
        )
        self.__uploaded_urls_to_sims[uploaded_url] = sims

        msg.append(sims)

    def __set_sfs(
        self,
        msg: Message,
        uploaded_url: str,
        path: Path,
        content_type: Optional[str] = None,
        caption: Optional[str] = None,
    ):
        cache = self.__uploaded_urls_to_sfs.get(uploaded_url)
        if cache:
            msg.append(cache)
            return

        sfs = self.xmpp["xep_0447"].get_sfs(path, [uploaded_url], content_type, caption)
        self.__uploaded_urls_to_sfs[uploaded_url] = sfs

        msg.append(sfs)

    def __send_url(
        self,
        msg: Message,
        legacy_msg_id: LegacyMessageType,
        uploaded_url: str,
        caption: Optional[str] = None,
        carbon=False,
        when: Optional[datetime] = None,
        **kwargs,
    ):
        msg["oob"]["url"] = uploaded_url
        msg["body"] = uploaded_url
        if caption:
            self._send(msg, carbon=carbon, **kwargs)
            self.send_text(
                caption, legacy_msg_id=legacy_msg_id, when=when, carbon=carbon, **kwargs
            )
        else:
            self._set_msg_id(msg, legacy_msg_id)
            self._send(msg, carbon=carbon, **kwargs)

    async def send_file(
        self,
        file_path: Optional[Union[Path, str]] = None,
        legacy_msg_id: Optional[LegacyMessageType] = None,
        *,
        data_stream: Optional[IO[bytes]] = None,
        data: Optional[bytes] = None,
        file_url: Optional[str] = None,
        file_name: Optional[str] = None,
        content_type: Optional[str] = None,
        reply_to_msg_id: Optional[LegacyMessageType] = None,
        reply_to_fallback_text: Optional[str] = None,
        reply_to_jid: Optional[JID] = None,
        when: Optional[datetime] = None,
        caption: Optional[str] = None,
        legacy_file_id: Optional[Union[str, int]] = None,
        thread: Optional[LegacyThreadType] = None,
        **kwargs,
    ):
        """
        Send a message with an attachment

        :param file_path: Path to the attachment
        :param data_stream: Alternatively, a stream of bytes (such as a File object)
        :param data: Alternatively, a bytes object
        :param file_url: Alternatively, a URL
        :param file_name: How the file should be named.
        :param content_type: MIME type, inferred from filename if not given
        :param legacy_msg_id: If you want to be able to transport read markers from the gateway
            user to the legacy network, specify this
        :param reply_to_msg_id: Quote another message (:xep:`0461`)
        :param reply_to_fallback_text: Fallback text for clients not supporting :xep:`0461`
        :param reply_to_jid: JID of the quoted message author
        :param when: when the file was sent, for a "delay" tag (:xep:`0203`)
        :param caption: an optional text that is linked to the file
        :param legacy_file_id: A unique identifier for the file on the legacy network.
             Plugins should try their best to provide it, to avoid duplicates.
        :param thread:
        """
        carbon = kwargs.pop("carbon", False)
        mto = kwargs.pop("mto", None)
        msg = self._make_message(
            when=when,
            reply_to_msg_id=reply_to_msg_id,
            reply_to_fallback_text=reply_to_fallback_text,
            reply_to_jid=reply_to_jid,
            carbon=carbon,
            mto=mto,
            thread=thread,
        )
        is_temp, local_path, new_url = await self.__get_url(
            Path(file_path) if file_path else None,
            data_stream,
            data,
            file_url,
            file_name,
            content_type,
            legacy_file_id,
        )

        if new_url is None:
            msg["body"] = (
                "I tried to send a file, but something went wrong. "
                "Tell your slidge admin to check the logs."
            )
            self._set_msg_id(msg, legacy_msg_id)
            self._send(msg, **kwargs)
            return

        if local_path:
            self.__set_sims(msg, new_url, local_path, content_type, caption)
            self.__set_sfs(msg, new_url, local_path, content_type, caption)
            if is_temp and isinstance(local_path, Path):
                local_path.unlink()
                local_path.parent.rmdir()

        self.__send_url(msg, legacy_msg_id, new_url, caption, carbon, when, **kwargs)
        return new_url


class ContentMessageMixin(AttachmentMixin):
    def send_text(
        self,
        body: str,
        legacy_msg_id: Optional[LegacyMessageType] = None,
        *,
        when: Optional[datetime] = None,
        reply_to_msg_id: Optional[LegacyMessageType] = None,
        reply_to_fallback_text: Optional[str] = None,
        reply_to_jid: Optional[JID] = None,
        thread: Optional[LegacyThreadType] = None,
        **kwargs,
    ):
        """
        Transmit a message from the entity to the user

        :param body: Context of the message
        :param legacy_msg_id: If you want to be able to transport read markers from the gateway
            user to the legacy network, specify this
        :param when: when the message was sent, for a "delay" tag (:xep:`0203`)
        :param reply_to_msg_id: Quote another message (:xep:`0461`)
        :param reply_to_fallback_text: Fallback text for clients not supporting :xep:`0461`
        :param reply_to_jid: JID of the quoted message author
        :param thread:
        """
        msg = self._make_message(
            mbody=body,
            legacy_msg_id=legacy_msg_id,
            when=when,
            reply_to_msg_id=reply_to_msg_id,
            reply_to_fallback_text=reply_to_fallback_text,
            reply_to_jid=reply_to_jid,
            hints=kwargs.get("hints") or {"markable", "store"},
            carbon=kwargs.get("carbon"),
            thread=thread,
        )
        self._send(msg, **kwargs)

    def correct(
        self,
        legacy_msg_id: LegacyMessageType,
        new_text: str,
        thread: Optional[LegacyThreadType] = None,
        **kwargs,
    ):
        """
        Call this when a legacy contact has modified his last message content.

        Uses last message correction (:xep:`0308`)

        :param legacy_msg_id: Legacy message ID this correction refers to
        :param new_text: The new text
        :param thread:
        """
        msg = self._make_message(
            mbody=new_text, carbon=kwargs.get("carbon"), thread=thread
        )
        msg["replace"]["id"] = self._legacy_to_xmpp(legacy_msg_id)
        self._send(msg, **kwargs)

    def react(
        self,
        legacy_msg_id: LegacyMessageType,
        emojis: Iterable[str] = (),
        thread: Optional[LegacyThreadType] = None,
        **kwargs,
    ):
        """
        Call this when a legacy contact reacts to a message

        :param legacy_msg_id: The message which the reaction refers to.
        :param emojis: An iterable of emojis used as reactions
        :param thread:
        """
        msg = self._make_message(
            hints={"store"}, carbon=kwargs.get("carbon"), thread=thread
        )
        xmpp_id = self._legacy_to_xmpp(legacy_msg_id)
        self.xmpp["xep_0444"].set_reactions(
            msg,
            to_id=xmpp_id,
            reactions=[remove_emoji_variation_selector_16(e) for e in emojis],
        )
        self._send(msg, **kwargs)

    def retract(
        self,
        legacy_msg_id: LegacyMessageType,
        thread: Optional[LegacyThreadType] = None,
        **kwargs,
    ):
        """
        Call this when a legacy contact retracts (:XEP:`0424`) a message

        :param legacy_msg_id: Legacy ID of the message to delete
        :param thread:
        """
        msg = self._make_message(
            state=None,
            hints={"store"},
            mbody=f"I have deleted the message {legacy_msg_id}, "
            "but your XMPP client does not support that",
            carbon=kwargs.get("carbon"),
            thread=thread,
        )
        msg.enable("fallback")
        msg["apply_to"]["id"] = self._legacy_to_xmpp(legacy_msg_id)
        msg["apply_to"].enable("retract")
        self._send(msg, **kwargs)


class CarbonMessageMixin(ContentMessageMixin, MarkerMixin):
    def _privileged_send(self, msg: Message):
        self.session.ignore_messages.add(msg.get_id())
        try:
            self.xmpp["xep_0356"].send_privileged_message(msg)
        except PermissionError:
            try:
                self.xmpp["xep_0356_old"].send_privileged_message(msg)
            except PermissionError:
                warnings.warn(
                    "Slidge does not have privileges to send message on behalf of user."
                    "Refer to https://slidge.readthedocs.io/en/latest/admin/xmpp_server.html "
                    "for more info."
                )


class MessageMixin(ChatStateMixin, MarkerMixin, ContentMessageMixin):
    pass


class MessageCarbonMixin(ChatStateMixin, CarbonMessageMixin):
    pass


log = logging.getLogger(__name__)
