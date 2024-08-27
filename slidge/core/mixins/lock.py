import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Hashable


class NamedLockMixin:
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.__locks = dict[Hashable, asyncio.Lock]()

    @asynccontextmanager
    async def lock(self, id_: Hashable):
        log.trace("getting %s", id_)  # type:ignore
        locks = self.__locks
        if not locks.get(id_):
            locks[id_] = asyncio.Lock()
        try:
            async with locks[id_]:
                log.trace("acquired %s", id_)  # type:ignore
                yield
        finally:
            log.trace("releasing %s", id_)  # type:ignore
            waiters = locks[id_]._waiters  # type:ignore
            if not waiters:
                del locks[id_]
                log.trace("erasing %s", id_)  # type:ignore

    def get_lock(self, id_: Hashable):
        return self.__locks.get(id_)


log = logging.getLogger(__name__)  # type:ignore
