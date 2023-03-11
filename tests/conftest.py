import hashlib
import io

from PIL import Image
from pathlib import Path

import pytest

from slidge.util import SubclassableOnce
from slidge.core.cache import avatar_cache

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

    request.cls.avatar_path = path
    request.cls.avatar_image = img
    request.cls.avatar_bytes = img_bytes
    request.cls.avatar_sha1 = hashlib.sha1(img_bytes).hexdigest()

    request.cls.avatar_original_sha1 = hashlib.sha1(path.read_bytes()).hexdigest()
