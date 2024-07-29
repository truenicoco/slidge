import asyncio
import hashlib
import io
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Optional

import aiohttp
from multidict import CIMultiDictProxy
from PIL.Image import Image
from PIL.Image import open as open_image
from sqlalchemy import select

from slidge.core import config
from slidge.db.models import Avatar
from slidge.db.store import AvatarStore
from slidge.util.types import URL, AvatarType


@dataclass
class CachedAvatar:
    pk: int
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
            pk=stored.id,
            filename=stored.filename,
            hash=stored.hash,
            height=stored.height,
            width=stored.width,
            etag=stored.etag,
            root=root_dir,
            last_modified=stored.last_modified,
        )


class NotModified(Exception):
    pass


class AvatarCache:
    dir: Path
    http: aiohttp.ClientSession
    store: AvatarStore

    def __init__(self):
        self._thread_pool = ThreadPoolExecutor(config.AVATAR_RESAMPLING_THREADS)

    def set_dir(self, path: Path):
        self.dir = path
        self.dir.mkdir(exist_ok=True)
        with self.store.session():
            for stored in self.store.get_all():
                avatar = CachedAvatar.from_store(stored, root_dir=path)
                if avatar.path.exists():
                    continue
                log.warning(
                    "Removing avatar %s from store because %s does not exist",
                    avatar.hash,
                    avatar.path,
                )
                self.store.delete_by_pk(stored.id)

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

    async def __download(
        self,
        url: str,
        headers: dict[str, str],
    ) -> tuple[Image, CIMultiDictProxy[str]]:
        async with self.http.get(url, headers=headers) as response:
            if response.status == HTTPStatus.NOT_MODIFIED:
                log.debug("Using avatar cache for %s", url)
                raise NotModified
            return (
                open_image(io.BytesIO(await response.read())),
                response.headers,
            )

    async def __is_modified(self, url, headers) -> bool:
        async with self.http.head(url, headers=headers) as response:
            return response.status != HTTPStatus.NOT_MODIFIED

    async def url_modified(self, url: URL) -> bool:
        cached = self.store.get_by_url(url)
        if cached is None:
            return True
        headers = self.__get_http_headers(cached)
        return await self.__is_modified(url, headers)

    def get_by_pk(self, pk: int) -> CachedAvatar:
        stored = self.store.get_by_pk(pk)
        assert stored is not None
        return CachedAvatar.from_store(stored, self.dir)

    @staticmethod
    async def _get_image(avatar: AvatarType) -> Image:
        if isinstance(avatar, bytes):
            return open_image(io.BytesIO(avatar))
        elif isinstance(avatar, Path):
            return open_image(avatar)
        raise TypeError("Avatar must be bytes or a Path", avatar)

    async def convert_or_get(self, avatar: AvatarType) -> CachedAvatar:
        if isinstance(avatar, (URL, str)):
            with self.store.session():
                stored = self.store.get_by_url(avatar)
                try:
                    img, response_headers = await self.__download(
                        avatar, self.__get_http_headers(stored)
                    )
                except NotModified:
                    assert stored is not None
                    return CachedAvatar.from_store(stored, self.dir)
        else:
            img = await self._get_image(avatar)
            response_headers = None
        with self.store.session() as orm:
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
                and isinstance(avatar, (str, Path))
                and (path := Path(avatar))
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

            stored = orm.execute(select(Avatar).where(Avatar.hash == hash_)).scalar()

            if stored is not None:
                return CachedAvatar.from_store(stored, self.dir)

            stored = Avatar(
                filename=filename,
                hash=hash_,
                height=img.height,
                width=img.width,
                url=avatar if isinstance(avatar, (URL, str)) else None,
            )
            if response_headers:
                stored.etag = response_headers.get("etag")
                stored.last_modified = response_headers.get("last-modified")

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
