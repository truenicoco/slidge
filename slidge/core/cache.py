import asyncio
import hashlib
import io
import logging
import shelve
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Optional

import aiohttp
from multidict import CIMultiDictProxy
from PIL import Image
from slixmpp import JID

from ..util.types import URL, LegacyFileIdType
from . import config


@dataclass
class CachedAvatar:
    filename: str
    hash: str
    height: int
    width: int
    root: Path
    etag: Optional[str] = None
    last_modified: Optional[str] = None

    @property
    def data(self):
        return self.path.read_bytes()

    @property
    def path(self):
        return self.root / self.filename


class AvatarCache:
    _shelf_path: str
    _jid_to_legacy_path: str
    dir: Path
    http: aiohttp.ClientSession

    def __init__(self):
        self._thread_pool = ThreadPoolExecutor(config.AVATAR_RESAMPLING_THREADS)

    def set_dir(self, path: Path):
        self.dir = path
        self.dir.mkdir(exist_ok=True)
        self._shelf_path = str(path / "slidge_avatar_cache.shelf")
        self._jid_to_legacy_path = str(path / "jid_to_avatar_unique_id.shelf")

    def close(self):
        self._thread_pool.shutdown(cancel_futures=True)

    def __get_http_headers(self, cached: Optional[CachedAvatar]):
        headers = {}
        if cached and (self.dir / cached.filename).exists():
            if last_modified := cached.last_modified:
                headers["If-Modified-Since"] = last_modified
            if etag := cached.etag:
                headers["If-None-Match"] = etag
        return headers

    async def get_avatar_from_url_alone(self, url: str, jid: JID):
        """
        Used when no avatar unique ID is passed. Store and use http headers
        to avoid fetching ut
        """
        cached = self.get(url)
        headers = self.__get_http_headers(cached)
        async with _download_lock:
            return await self.__download(cached, url, headers, jid)

    async def __download(
        self,
        cached: Optional[CachedAvatar],
        url: str,
        headers: dict[str, str],
        jid: JID,
    ):
        async with self.http.get(url, headers=headers) as response:
            if response.status == HTTPStatus.NOT_MODIFIED:
                log.debug("Using avatar cache for %s", jid)
                return cached
            log.debug("Download avatar for %s", jid)
            return await self.convert_and_store(
                Image.open(io.BytesIO(await response.read())),
                url,
                jid,
                response.headers,
            )

    async def url_has_changed(self, url: URL):
        with shelve.open(self._shelf_path) as s:
            cached = s.get(url)
        if cached is None:
            return True
        headers = self.__get_http_headers(cached)
        async with self.http.head(url, headers=headers) as response:
            return response.status != HTTPStatus.NOT_MODIFIED

    def get(self, unique_id: LegacyFileIdType) -> Optional[CachedAvatar]:
        with shelve.open(self._shelf_path) as s:
            return s.get(str(unique_id))

    def get_cached_id_for(self, jid: JID) -> Optional[LegacyFileIdType]:
        with shelve.open(self._jid_to_legacy_path) as s:
            return s.get(str(jid))

    def store_jid(self, jid: JID, uid: LegacyFileIdType):
        with shelve.open(self._jid_to_legacy_path) as s:
            s[str(jid)] = uid

    def delete_jid(self, jid: JID):
        try:
            with shelve.open(self._jid_to_legacy_path) as s:
                del s[str(jid)]
        except KeyError:
            pass

    async def convert_and_store(
        self,
        img: Image.Image,
        unique_id: LegacyFileIdType,
        jid: JID,
        response_headers: Optional[CIMultiDictProxy[str]] = None,
    ) -> CachedAvatar:
        resize = (size := config.AVATAR_SIZE) and any(x > size for x in img.size)
        if resize:
            await asyncio.get_event_loop().run_in_executor(
                self._thread_pool, img.thumbnail, (size, size)
            )
            log.debug("Resampled image to %s", img.size)

        filename = str(uuid.uuid1()) + ".png"
        file_path = self.dir / filename

        if (
            not resize
            and img.format == "PNG"
            and isinstance(unique_id, str)
            and (path := Path(unique_id))
            and path.exists()
        ):
            img_bytes = path.read_bytes()
        else:
            with io.BytesIO() as f:
                img.save(f, format="PNG")
                img_bytes = f.getvalue()

        with file_path.open("wb") as file:
            file.write(img_bytes)

        hash_ = hashlib.sha1(img_bytes).hexdigest()

        avatar = CachedAvatar(
            filename=filename,
            hash=hash_,
            height=img.height,
            width=img.width,
            root=self.dir,
        )
        if response_headers:
            avatar.etag = response_headers.get("etag")
            avatar.last_modified = response_headers.get("last-modified")
        with shelve.open(self._shelf_path) as s:
            s[str(unique_id)] = avatar
        self.store_jid(jid, unique_id)
        return avatar


avatar_cache = AvatarCache()
log = logging.getLogger(__name__)
_download_lock = asyncio.Lock()

__all__ = (
    "CachedAvatar",
    "avatar_cache",
)
