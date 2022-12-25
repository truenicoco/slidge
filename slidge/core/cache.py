import hashlib
import io
import logging
import shelve
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Optional

import aiohttp
from multidict import CIMultiDictProxy
from PIL import Image

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
        return (self.root / self.filename).read_bytes()


class AvatarCache:
    _shelf: shelve.Shelf[CachedAvatar]
    _dir: Path

    def set_dir(self, path: Path):
        self._dir = path
        self._dir.mkdir(exist_ok=True)
        self._shelf = shelve.open(str(path / "slidge_url_avatars.shelf"))  # type: ignore

    def close(self):
        self._shelf.sync()
        self._shelf.close()

    async def get_avatar(self, url: str):
        cached = self._shelf.get(url)
        headers = {}
        if cached and (self._dir / cached.filename).exists():
            if last_modified := cached.last_modified:
                headers["If-Modified-Since"] = last_modified
            if etag := cached.etag:
                headers["If-None-Match"] = etag
        log.debug("Request headers: %s", headers)

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                log.debug("Response headers: %s", response.headers)
                if response.status == HTTPStatus.NOT_MODIFIED:
                    log.debug("Using avatar cache")
                    return cached
                log.debug("Download avatar")
                return await self._convert_and_store(
                    Image.open(io.BytesIO(await response.read())), url, response.headers
                )

    async def _convert_and_store(
        self, img: Image.Image, url: str, response_headers: CIMultiDictProxy[str]
    ):
        if (size := config.AVATAR_SIZE) and any(x > size for x in img.size):
            img.thumbnail((size, size))
            log.debug("Resampled image to %s", img.size)

        filename = str(uuid.uuid1())
        file_path = self._dir / filename

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
            etag=response_headers.get("etag"),
            last_modified=response_headers.get("last-modified"),
            root=self._dir,
        )
        self._shelf[url] = avatar
        self._shelf.sync()
        return avatar


avatar_cache = AvatarCache()
log = logging.getLogger(__name__)
