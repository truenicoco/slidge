import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Hashable


class NamedLockMixin:
    def __init__(self):
        self.__locks = dict[Hashable, asyncio.Lock]()

    @asynccontextmanager
    async def get_lock(self, id_: Hashable):
        log.debug("getting %s", id_)
        locks = self.__locks
        if not locks.get(id_):
            locks[id_] = asyncio.Lock()
        async with locks[id_]:
            log.debug("acquired %s", id_)
            yield
        log.debug("releasing %s", id_)
        waiters = locks[id_]._waiters  # type:ignore
        if not waiters:
            del locks[id_]
            log.debug("erasing %s", id_)


log = logging.getLogger(__name__)
