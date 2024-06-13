import asyncio
import hashlib
import io
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Callable, Optional

import aiohttp
from black import Any
from multidict import CIMultiDictProxy
from PIL import Image
from slixmpp import JID

from ..db.models import Avatar
from ..db.store import AvatarStore
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

    @staticmethod
    def from_store(stored: Avatar, root_dir: Path):
        return CachedAvatar(
            filename=stored.filename,
            hash=stored.hash,
            height=stored.height,
            width=stored.width,
            etag=stored.etag,
            root=root_dir,
            last_modified=stored.last_modified,
        )


class AvatarCache:
    _shelf_path: str
    _jid_to_legacy_path: str
    dir: Path
    http: aiohttp.ClientSession
    store: AvatarStore
    legacy_avatar_type: Callable[[str], Any] = str

    def __init__(self):
        self._thread_pool = ThreadPoolExecutor(config.AVATAR_RESAMPLING_THREADS)

    def set_dir(self, path: Path):
        self.dir = path
        self.dir.mkdir(exist_ok=True)

    def close(self):
        self._thread_pool.shutdown(cancel_futures=True)

    def __get_http_headers(self, cached: Optional[CachedAvatar | Avatar]):
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
        cached = self.store.get_by_url(url)
        if cached is None:
            return True
        headers = self.__get_http_headers(cached)
        async with self.http.head(url, headers=headers) as response:
            return response.status != HTTPStatus.NOT_MODIFIED

    def get(self, unique_id: LegacyFileIdType) -> Optional[CachedAvatar]:
        stored = self.store.get_by_legacy_id(str(unique_id))
        if stored is None:
            return None
        return CachedAvatar.from_store(stored, self.dir)

    def get_cached_id_for(self, jid: JID) -> Optional[LegacyFileIdType]:
        with self.store.session():
            stored = self.store.get_by_jid(jid)
            if stored is None:
                return None
            if stored.legacy_id is None:
                return None
            return self.legacy_avatar_type(stored.legacy_id)

    def store_jid(self, jid: JID, uid: LegacyFileIdType):
        with self.store.session() as orm:
            stored = self.store.get_by_legacy_id(str(uid))
            assert stored is not None
            stored.jid = jid
            orm.add(stored)
            orm.commit()

    def delete_jid(self, jid: JID):
        with self.store.session() as orm:
            stored = self.store.get_by_jid(jid)
            if stored is None:
                return
            orm.delete(stored)
            orm.commit()

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

        stored = Avatar(
            filename=filename,
            hash=hash_,
            height=img.height,
            width=img.width,
            jid=jid,
            legacy_id=None if unique_id is None else str(unique_id),
            url=None if response_headers is None else unique_id,
        )
        if response_headers:
            stored.etag = response_headers.get("etag")
            stored.last_modified = response_headers.get("last-modified")
        with self.store.session() as orm:
            orm.add(stored)
            orm.commit()
            return CachedAvatar.from_store(stored, self.dir)


avatar_cache = AvatarCache()
log = logging.getLogger(__name__)
_download_lock = asyncio.Lock()

__all__ = (
    "CachedAvatar",
    "avatar_cache",
)
