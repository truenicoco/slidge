import logging
import shutil
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from slixmpp import JID

from .core import config
from .db.meta import get_engine
from .db.models import GatewayUser
from .db.store import SlidgeStore


def remove_avatar_cache_v1():
    old_dir = config.HOME_DIR / "slidge_avatars"
    if old_dir.exists():
        log.info("Avatar cache dir v1 found, clearing it.")
        shutil.rmtree(old_dir)


def get_alembic_cfg() -> Config:
    alembic_cfg = Config()
    alembic_cfg.set_section_option(
        "alembic",
        "script_location",
        str(Path(__file__).parent / "db" / "alembic"),
    )
    return alembic_cfg


def remove_resource_parts_from_users() -> None:
    with SlidgeStore(get_engine(config.DB_URL)).session() as orm:
        for user in orm.query(GatewayUser).all():
            if user.jid.resource:
                user.jid = JID(user.jid.bare)
                orm.add(user)
        orm.commit()


def migrate() -> None:
    remove_avatar_cache_v1()
    command.upgrade(get_alembic_cfg(), "head")
    remove_resource_parts_from_users()


def main():
    """
    Updates the (dev) database in ./dev/slidge.sqlite and generates a revision

    Usage: python -m slidge.migration "Revision message blah blah blah"
    """
    alembic_cfg = get_alembic_cfg()
    command.upgrade(alembic_cfg, "head")
    command.revision(alembic_cfg, sys.argv[1], autogenerate=True)


log = logging.getLogger(__name__)

if __name__ == "__main__":
    main()
