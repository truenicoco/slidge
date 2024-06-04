import hashlib
import io
from base64 import b64encode
from contextlib import asynccontextmanager
from http import HTTPStatus
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image
from slixmpp import JID

from slidge.core.cache import avatar_cache
from slidge.util import SubclassableOnce
from slidge.util.sql import db

SubclassableOnce.TEST_MODE = True


@pytest.fixture
def MockRE():
    class MockRE:
        @staticmethod
        def match(*a, **kw):
            return True

    return MockRE


@pytest.fixture(autouse=True)
def cache_dir(tmp_path):
    avatar_cache.set_dir(tmp_path)


@pytest.fixture(scope="class")
def avatar(request):
    path = Path(__file__).parent.parent / "dev" / "assets" / "5x5.png"
    img = Image.open(path)
    with io.BytesIO() as f:
        img.save(f, format="PNG")
        img_bytes = f.getvalue()

    class MockResponse:
        def __init__(self, status):
            self.status = status

        @staticmethod
        async def read():
            return img_bytes

        headers = {"etag": "etag", "last-modified": "last"}

    @asynccontextmanager
    async def mock_get(url, headers=None):
        assert url == "AVATAR_URL"
        if headers and (
            headers.get("If-None-Match") == "etag"
            or headers.get("If-Modified-Since") == "last"
        ):
            yield MockResponse(HTTPStatus.NOT_MODIFIED)
        else:
            yield MockResponse(HTTPStatus.OK)

    request.cls.avatar_path = path
    request.cls.avatar_image = img
    request.cls.avatar_bytes = img_bytes
    request.cls.avatar_sha1 = hashlib.sha1(img_bytes).hexdigest()
    request.cls.avatar_url = "AVATAR_URL"

    request.cls.avatar_base64 = b64encode(img_bytes).decode("utf-8")
    request.cls.avatar_original_sha1 = hashlib.sha1(path.read_bytes()).hexdigest()

    with patch("slidge.core.cache.avatar_cache.http", create=True) as mock:
        mock.get = mock_get
        mock.head = mock_get
        yield request


# just to have typings for the fixture which pycharm does not understand
class AvatarFixtureMixin:
    avatar_path: Path
    avatar_image: Image
    avatar_bytes: bytes
    avatar_sha1: str
    avatar_original_sha1: str
    avatar_url: str
    avatar_base64: str


@pytest.fixture
def user():
    user = MagicMock()
    user.jid = JID("test@test.fr")
    db.user_store(user.jid)
    yield user
    db.user_del(user.jid)


@pytest.fixture(scope="class")
def user_cls(request):
    user = MagicMock()
    user.jid = JID("test@test.fr")
    db.user_store(user.jid)
    request.cls.user = user
    yield user
    db.user_del(user.jid)
