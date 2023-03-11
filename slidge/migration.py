import logging
import shutil

from .core import config


def remove_avatar_cache_v1():
    old_dir = config.HOME_DIR / "slidge_avatars"
    if old_dir.exists():
        log.info("Avatar cache dir v1 found, clearing it.")
        shutil.rmtree(old_dir)


def migrate():
    remove_avatar_cache_v1()


log = logging.getLogger(__name__)
