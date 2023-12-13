import logging
import os
import sqlite3
import tempfile
from asyncio import AbstractEventLoop, Task, sleep
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from time import time
from typing import (
    TYPE_CHECKING,
    Collection,
    Generic,
    Iterator,
    NamedTuple,
    Optional,
    TypeVar,
    Union,
)

from slixmpp import JID
from slixmpp.exceptions import XMPPError
from slixmpp.types import PresenceShows, PresenceTypes

from ..core import config
from .archive_msg import HistoryMessage

if TYPE_CHECKING:
    from .db import GatewayUser

KeyType = TypeVar("KeyType")
ValueType = TypeVar("ValueType")


class CachedPresence(NamedTuple):
    last_seen: Optional[datetime] = None
    ptype: Optional[PresenceTypes] = None
    pstatus: Optional[str] = None
    pshow: Optional[PresenceShows] = None


class MamMetadata(NamedTuple):
    id: str
    sent_on: datetime


class Base:
    def __init__(self):
        handler, filename = tempfile.mkstemp()

        os.close(handler)
        self.__filename = filename

        self.con = sqlite3.connect(filename)
        self.cur = self.con.cursor()
        self.cur.executescript((Path(__file__).parent / "schema.sql").read_text())

        self.__mam_cleanup_task: Optional[Task] = None

    def __del__(self):
        self.con.close()
        os.unlink(self.__filename)


class MAMMixin(Base):
    def __init__(self):
        super().__init__()
        self.__mam_cleanup_task: Optional[Task] = None
        self.__msg_cur = msg_cur = self.con.cursor()
        msg_cur.row_factory = self.__msg_factory  # type:ignore
        self.__metadata_cur = metadata_cur = self.con.cursor()
        metadata_cur.row_factory = self.__metadata_factory  # type:ignore

    @staticmethod
    def __msg_factory(_cur, row: tuple[str, float]) -> HistoryMessage:
        return HistoryMessage(
            row[0], when=datetime.fromtimestamp(row[1], tz=timezone.utc)
        )

    @staticmethod
    def __metadata_factory(_cur, row: tuple[str, float]) -> MamMetadata:
        return MamMetadata(row[0], datetime.fromtimestamp(row[1], tz=timezone.utc))

    def mam_nuke(self):
        self.cur.execute("DELETE FROM mam_message")
        self.con.commit()

    def mam_add_muc(self, jid: str, user: "GatewayUser"):
        try:
            self.cur.execute(
                "INSERT INTO "
                "muc(jid, user_id) "
                "VALUES("
                "  ?, "
                "  (SELECT id FROM user WHERE jid = ?)"
                ")",
                (jid, user.bare_jid),
            )
        except sqlite3.IntegrityError:
            log.debug("Tried to add a MUC that was already here: (%s, %s)", user, jid)
        else:
            self.con.commit()

    def mam_add_msg(self, muc_jid: str, msg: "HistoryMessage", user: "GatewayUser"):
        self.cur.execute(
            "REPLACE INTO "
            "mam_message(message_id, sender_jid, sent_on, xml, muc_id, user_id)"
            "VALUES(?, ?, ?, ?,"
            "(SELECT id FROM muc WHERE jid = ?),"
            "(SELECT id FROM user WHERE jid = ?)"
            ")",
            (
                msg.id,
                str(msg.stanza.get_from()),
                msg.when.timestamp(),
                str(msg.stanza),
                muc_jid,
                user.bare_jid,
            ),
        )
        self.con.commit()

    def mam_launch_cleanup_task(self, loop: AbstractEventLoop):
        self.__mam_cleanup_task = loop.create_task(self.__mam_cleanup())

    async def __mam_cleanup(self):
        await sleep(6 * 3600)
        self.mam_cleanup()

    def mam_cleanup(self):
        self.cur.execute(
            "DELETE FROM mam_message WHERE sent_on < ?",
            (time() - config.MAM_MAX_DAYS * 24 * 3600,),
        )
        self.con.commit()

    def __mam_get_sent_on(self, muc_jid: str, mid: str, user: "GatewayUser"):
        res = self.cur.execute(
            "SELECT sent_on "
            "FROM mam_message "
            "WHERE message_id = ? "
            "AND muc_id = (SELECT id FROM muc WHERE jid = ?) "
            "AND user_id = (SELECT id FROM user WHERE jid = ?)",
            (mid, muc_jid, user.bare_jid),
        )
        row = res.fetchone()
        if row is None:
            raise XMPPError("item-not-found", f"Message {mid} not found")
        return row[0]

    def __mam_bound(
        self,
        muc_jid: str,
        user: "GatewayUser",
        date: Optional[datetime] = None,
        id_: Optional[str] = None,
        comparator=min,
    ):
        if id_ is not None:
            after_id_sent_on = self.__mam_get_sent_on(muc_jid, id_, user)
            if date:
                timestamp = comparator(after_id_sent_on, date.timestamp())
            else:
                timestamp = after_id_sent_on
            return " AND sent_on > ?", timestamp
        elif date is None:
            raise TypeError
        else:
            return " AND sent_on >= ?", date.timestamp()

    def mam_get_messages(
        self,
        user: "GatewayUser",
        muc_jid: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        before_id: Optional[str] = None,
        after_id: Optional[str] = None,
        ids: Collection[str] = (),
        last_page_n: Optional[int] = None,
        sender: Optional[str] = None,
        flip=False,
    ) -> Iterator[HistoryMessage]:
        query = (
            "SELECT xml, sent_on FROM mam_message "
            "WHERE muc_id = (SELECT id FROM muc WHERE jid = ?) "
            "AND user_id = (SELECT id FROM user WHERE jid = ?) "
        )
        params: list[Union[str, float, int]] = [muc_jid, user.bare_jid]

        if start_date or after_id:
            subquery, timestamp = self.__mam_bound(
                muc_jid, user, start_date, after_id, max
            )
            query += subquery
            params.append(timestamp)
        if end_date or before_id:
            subquery, timestamp = self.__mam_bound(
                muc_jid, user, end_date, before_id, min
            )
            query += subquery
            params.append(timestamp)
        if sender:
            query += " AND sender_jid = ?"
            params.append(sender)
        if ids:
            query += f" AND message_id IN ({','.join('?' * len(ids))})"
            params.extend(ids)
        if last_page_n:
            # TODO: optimize query further when <flip> and last page are
            #       combined.
            query = f"SELECT * FROM ({query} ORDER BY sent_on DESC LIMIT ?)"
            params.append(last_page_n)
        query += " ORDER BY sent_on"
        if flip:
            query += " DESC"

        res = self.__msg_cur.execute(query, params)

        if ids:
            rows = res.fetchall()
            if len(rows) != len(ids):
                raise XMPPError(
                    "item-not-found",
                    "One of the requested messages IDs could not be found "
                    "with the given constraints.",
                )
            for row in rows:
                yield row

        while row := res.fetchone():
            yield row

    def mam_get_first_and_last(self, muc_jid: str) -> list[MamMetadata]:
        res = self.__metadata_cur.execute(
            "SELECT message_id, sent_on "
            "FROM mam_message "
            "JOIN muc ON muc.jid = ? "
            "WHERE sent_on = (SELECT MIN(sent_on) FROM mam_message WHERE muc_id = muc.id) "
            "   OR sent_on = (SELECT MAX(sent_on) FROM mam_message WHERE muc_id = muc.id) "
            " ORDER BY sent_on",
            (muc_jid,),
        )
        return res.fetchall()


class AttachmentMixin(Base):
    def attachment_remove(self, legacy_id):
        self.cur.execute("DELETE FROM attachment WHERE legacy_id = ?", (legacy_id,))
        self.con.commit()

    def attachment_store_url(self, legacy_id, url: str):
        self.cur.execute(
            "REPLACE INTO attachment(legacy_id, url) VALUES (?,?)", (legacy_id, url)
        )
        self.con.commit()

    def attachment_store_sims(self, url: str, sims: str):
        self.cur.execute("UPDATE attachment SET sims = ? WHERE url = ?", (sims, url))
        self.con.commit()

    def attachment_store_sfs(self, url: str, sfs: str):
        self.cur.execute("UPDATE attachment SET sfs = ? WHERE url = ?", (sfs, url))
        self.con.commit()

    def attachment_get_url(self, legacy_id):
        res = self.cur.execute(
            "SELECT url FROM attachment WHERE legacy_id = ?", (legacy_id,)
        )
        return first_of_tuple_or_none(res.fetchone())

    def attachment_get_sims(self, url: str):
        res = self.cur.execute("SELECT sims FROM attachment WHERE url = ?", (url,))
        return first_of_tuple_or_none(res.fetchone())

    def attachment_get_sfs(self, url: str):
        res = self.cur.execute("SELECT sfs FROM attachment WHERE url = ?", (url,))
        return first_of_tuple_or_none(res.fetchone())

    def attachment_store_legacy_to_multi_xmpp_msg_ids(
        self, legacy_id, xmpp_ids: list[str]
    ):
        with self.con:
            res = self.cur.execute(
                "INSERT OR IGNORE INTO attachment_legacy_msg_id(legacy_id) VALUES (?)",
                (legacy_id,),
            )
            row_id = res.lastrowid
            # for xmpp_id in xmpp_ids:
            self.cur.executemany(
                "INSERT INTO attachment_xmpp_ids(legacy_msg_id, xmpp_id) VALUES (?, ?)",
                ((row_id, i) for i in xmpp_ids),
            )

    def attachment_get_xmpp_ids_for_legacy_msg_id(self, legacy_id) -> list:
        res = self.cur.execute(
            "SELECT xmpp_id FROM attachment_xmpp_ids "
            "WHERE legacy_msg_id = (SELECT id FROM attachment_legacy_msg_id WHERE legacy_id = ?)",
            (legacy_id,),
        )
        return [r[0] for r in res.fetchall()]

    def attachment_get_associated_xmpp_ids(self, xmpp_id: str):
        res = self.cur.execute(
            "SELECT xmpp_id FROM attachment_xmpp_ids "
            "WHERE legacy_msg_id = "
            "(SELECT legacy_msg_id FROM attachment_xmpp_ids WHERE xmpp_id = ?)",
            (xmpp_id,),
        )
        return [r[0] for r in res.fetchall() if r[0] != xmpp_id]

    def attachment_get_legacy_id_for_xmpp_id(self, xmpp_id: str):
        res = self.cur.execute(
            "SELECT legacy_id FROM attachment_legacy_msg_id "
            "WHERE id = (SELECT legacy_msg_id FROM attachment_xmpp_ids WHERE xmpp_id = ?)",
            (xmpp_id,),
        )
        return first_of_tuple_or_none(res.fetchone())


class NickMixin(Base):
    def nick_get(self, jid: JID, user: "GatewayUser"):
        res = self.cur.execute(
            "SELECT nick FROM nick "
            "WHERE jid = ? "
            "AND user_id = (SELECT id FROM user WHERE jid = ?)",
            (str(jid), user.bare_jid),
        )
        return first_of_tuple_or_none(res.fetchone())

    def nick_store(self, jid: JID, nick: str, user: "GatewayUser"):
        self.cur.execute(
            "REPLACE INTO nick(jid, nick, user_id) "
            "VALUES (?,?,(SELECT id FROM user WHERE jid = ?))",
            (str(jid), nick, user.bare_jid),
        )
        self.con.commit()


class AvatarMixin(Base):
    def avatar_get(self, jid: JID):
        res = self.cur.execute(
            "SELECT cached_id FROM avatar WHERE jid = ?", (str(jid),)
        )
        return first_of_tuple_or_none(res.fetchone())

    def avatar_store(self, jid: JID, cached_id: Union[int, str]):
        self.cur.execute(
            "REPLACE INTO avatar(jid, cached_id) VALUES (?,?)", (str(jid), cached_id)
        )
        self.con.commit()

    def avatar_delete(self, jid: JID):
        self.cur.execute("DELETE FROM avatar WHERE jid = ?", (str(jid),))
        self.con.commit()


class PresenceMixin(Base):
    def __init__(self):
        super().__init__()
        self.__cur = cur = self.con.cursor()
        cur.row_factory = self.__row_factory  # type:ignore

    @staticmethod
    def __row_factory(
        _cur: sqlite3.Cursor,
        row: tuple[
            Optional[int],
            Optional[PresenceTypes],
            Optional[str],
            Optional[PresenceShows],
        ],
    ):
        if row[0] is not None:
            last_seen = datetime.fromtimestamp(row[0], tz=timezone.utc)
        else:
            last_seen = None
        return CachedPresence(last_seen, *row[1:])

    def presence_nuke(self):
        # useful for tests
        self.cur.execute("DELETE FROM presence")
        self.con.commit()

    def presence_store(self, jid: JID, presence: CachedPresence, user: "GatewayUser"):
        self.cur.execute(
            "REPLACE INTO presence(jid, last_seen, ptype, pstatus, pshow, user_id) "
            "VALUES (?,?,?,?,?,(SELECT id FROM user WHERE jid = ?))",
            (
                str(jid),
                presence[0].timestamp() if presence[0] else None,
                *presence[1:],
                user.bare_jid,
            ),
        )
        self.con.commit()

    def presence_delete(self, jid: JID, user: "GatewayUser"):
        self.cur.execute(
            "DELETE FROM presence WHERE (jid = ? and user_id = (SELECT id FROM user WHERE jid = ?))",
            (str(jid), user.bare_jid),
        )
        self.con.commit()

    def presence_get(self, jid: JID, user: "GatewayUser") -> Optional[CachedPresence]:
        return self.__cur.execute(
            "SELECT last_seen, ptype, pstatus, pshow FROM presence "
            "WHERE jid = ? AND user_id = (SELECT id FROM user WHERE jid = ?)",
            (str(jid), user.bare_jid),
        ).fetchone()


class UserMixin(Base):
    def user_store(self, user: "GatewayUser"):
        try:
            self.cur.execute("INSERT INTO user(jid) VALUES (?)", (user.bare_jid,))
        except sqlite3.IntegrityError:
            log.debug("User has already been added.")
        else:
            self.con.commit()

    def user_del(self, user: "GatewayUser"):
        self.cur.execute("DELETE FROM user WHERE jid = ?", (user.bare_jid,))
        self.con.commit()


def first_of_tuple_or_none(x: Optional[tuple]):
    if x is None:
        return None
    return x[0]


class SQLBiDict(Generic[KeyType, ValueType]):
    def __init__(
        self,
        table: str,
        key1: str,
        key2: str,
        user: "GatewayUser",
        sql: Optional[Base] = None,
        create_table=False,
        is_inverse=False,
    ):
        if sql is None:
            sql = db
        self.db = sql
        self.table = table
        self.key1 = key1
        self.key2 = key2
        self.user = user
        if create_table:
            sql.cur.execute(
                f"CREATE TABLE {table} (id "
                "INTEGER PRIMARY KEY,"
                "user_id INTEGER,"
                f"{key1} UNIQUE,"
                f"{key2} UNIQUE,"
                f"FOREIGN KEY(user_id) REFERENCES user(id))",
            )
        if is_inverse:
            return
        self.inverse = SQLBiDict[ValueType, KeyType](
            table, key2, key1, user, sql=sql, is_inverse=True
        )

    def __setitem__(self, key: KeyType, value: ValueType):
        self.db.cur.execute(
            f"REPLACE INTO {self.table}"
            f"(user_id, {self.key1}, {self.key2}) "
            "VALUES ((SELECT id FROM user WHERE jid = ?), ?, ?)",
            (self.user.bare_jid, key, value),
        )
        self.db.con.commit()

    def __getitem__(self, item: KeyType) -> ValueType:
        v = self.get(item)
        if v is None:
            raise KeyError(item)
        return v

    def __contains__(self, item: KeyType) -> bool:
        res = self.db.cur.execute(
            f"SELECT {self.key1} FROM {self.table} "
            f"WHERE {self.key1} = ? AND user_id = (SELECT id FROM user WHERE jid = ?)",
            (item, self.user.bare_jid),
        ).fetchone()
        return res is not None

    @lru_cache(100)
    def get(self, item: KeyType) -> Optional[ValueType]:
        res = self.db.cur.execute(
            f"SELECT {self.key2} FROM {self.table} "
            f"WHERE {self.key1} = ? AND user_id = (SELECT id FROM user WHERE jid = ?)",
            (item, self.user.bare_jid),
        ).fetchone()
        if res is None:
            return res
        return res[0]


class TemporaryDB(
    AvatarMixin, AttachmentMixin, NickMixin, MAMMixin, UserMixin, PresenceMixin
):
    pass


db = TemporaryDB()
log = logging.getLogger(__name__)
