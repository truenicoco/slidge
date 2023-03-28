import functools
import logging
import os
import shutil
import stat
import tempfile
import warnings
from datetime import datetime
from pathlib import Path
from typing import IO, Collection, Optional, Union
from uuid import uuid4

from slixmpp import Message
from slixmpp.exceptions import IqError
from slixmpp.plugins.xep_0363 import FileUploadError

from ...util import BiDict
from ...util.types import (
    LegacyAttachment,
    LegacyMessageType,
    LegacyThreadType,
    MessageReference,
)
from ...util.util import fix_suffix
from ...util.xep_0385.stanza import Sims
from ...util.xep_0447.stanza import StatelessFileSharing
from .. import config
from .message_maker import MessageMaker


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
                    (
                        "There are several or zero files in %s, "
                        "slidge doesn't know which one to pick among %s"
                    ),
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

        if file_name and len(file_name) > config.ATTACHMENT_MAXIMUM_FILE_NAME_LENGTH:
            log.debug("Trimming long filename: %s", file_name)
            if "." in file_name:
                base, suffix = file_name.split(".")
                suffix = "." + suffix
            else:
                base = file_name
                suffix = "."
            file_name = (
                base[: config.ATTACHMENT_MAXIMUM_FILE_NAME_LENGTH] + "." + suffix
            )

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
        reply_to: Optional[MessageReference] = None,
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
        :param reply_to: Quote another message (:xep:`0461`)
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
            reply_to=reply_to,
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

    def __send_body(
        self,
        body: Optional[str] = None,
        legacy_msg_id: Optional[LegacyMessageType] = None,
        reply_to: Optional[MessageReference] = None,
        when: Optional[datetime] = None,
        thread: Optional[LegacyThreadType] = None,
        **kwargs,
    ):
        if body:
            self.send_text(
                body,
                legacy_msg_id,
                reply_to=reply_to,
                when=when,
                thread=thread,
                **kwargs,
            )

    async def send_files(
        self,
        attachments: Collection[LegacyAttachment],
        legacy_msg_id: Optional[LegacyMessageType] = None,
        body: Optional[str] = None,
        *,
        reply_to: Optional[MessageReference] = None,
        when: Optional[datetime] = None,
        thread: Optional[LegacyThreadType] = None,
        body_first=False,
        correction=False,
        **kwargs,
    ):
        # TODO: once the epic XEP-0385 vs XEP-0447 battle is over, pick
        #       one and stop sending several attachments this way
        # we attach the legacy_message ID to the last message we send, because
        # we don't want several messages with the same ID (especially for MUC MAM)
        # TODO: add a correction argument to the signature, rename this to
        #       send_rich_message and ditch send_text() and correct()
        if not attachments and not body:
            # ignoring empty message
            return
        send_body = functools.partial(
            self.__send_body,
            body=body,
            reply_to=reply_to,
            when=when,
            thread=thread,
            correction=correction,
            legacy_msg_id=legacy_msg_id,
            **kwargs,
        )
        if body_first:
            send_body()
        last_attachment_i = len(attachments) - 1
        for i, attachment in enumerate(attachments):
            last = i == last_attachment_i
            await self.send_file(
                file_path=attachment.path,
                legacy_msg_id=legacy_msg_id if last and not body else None,
                file_url=attachment.url,
                data_stream=attachment.stream,
                data=attachment.data,
                reply_to=reply_to,
                when=when,
                thread=thread,
                **kwargs,
            )
        if not body_first:
            send_body()


log = logging.getLogger(__name__)
