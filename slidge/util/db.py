# here to allow migration of the user store from v0.1
# since it relies on shelf, which relies on pickle, we need to keep objects
# importable where they were when the shelf was written

from ..db.alembic.old_user_store import *  # noqa:F403
