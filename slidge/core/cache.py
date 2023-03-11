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
        return self.path.read_bytes()

    @property
    def path(self):
        return self.root / self.filename


class AvatarCache:
    _shelf: shelve.Shelf[CachedAvatar]
    dir: Path

    def set_dir(self, path: Path):
        self.dir = path
        self.dir.mkdir(exist_ok=True)
        self._shelf = shelve.open(str(path / "slidge_avatar_cache.shelf"))  # type: ignore

    def close(self):
        self._shelf.sync()
        self._shelf.close()

    async def get_avatar_from_url_alone(self, url: str):
        """
        Used when no avatar unique ID is passed. Store and use http headers
        to avoid fetching ut
        """
        cached = self._shelf.get(url)
        headers = {}
        if cached and (self.dir / cached.filename).exists():
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
                return self.convert_and_store(
                    Image.open(io.BytesIO(await response.read())), url, response.headers
                )

    def get(self, unique_id: str):
        return self._shelf.get(unique_id)

    def convert_and_store(
        self,
        img: Image.Image,
        unique_id: str,
        response_headers: Optional[CIMultiDictProxy[str]] = None,
    ):
        if (size := config.AVATAR_SIZE) and any(x > size for x in img.size):
            img.thumbnail((size, size))
            log.debug("Resampled image to %s", img.size)

        filename = str(uuid.uuid1()) + ".png"
        file_path = self.dir / filename

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
        self._shelf[unique_id] = avatar
        self._shelf.sync()
        return avatar


avatar_cache = AvatarCache()
log = logging.getLogger(__name__)
