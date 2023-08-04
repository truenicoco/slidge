import os
import sqlite3
import tempfile
from asyncio import AbstractEventLoop, Task, sleep
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from time import time
from typing import TYPE_CHECKING, Collection, Generic, Optional, Union

from slixmpp.exceptions import XMPPError

from ..core import config
from .util import KeyType, ValueType

if TYPE_CHECKING:
    from slidge.core.muc.archive import HistoryMessage


class TemporaryDB:
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

    def mam_nuke(self):
        # useful for tests
        self.cur.execute("DELETE FROM mam_message")
        self.con.commit()

    def mam_add_muc(self, jid: str):
        self.cur.execute("INSERT INTO muc(jid) VALUES(?)", (jid,))
        self.con.commit()

    def mam_add_msg(self, muc_jid: str, msg: "HistoryMessage"):
        self.cur.execute(
            """
            INSERT INTO
                mam_message(message_id, sender_jid, sent_on, xml, muc_id)
            VALUES
                (?, ?, ?, ?, (SELECT id FROM muc WHERE jid = ?))
            """,
            (
                msg.id,
                str(msg.stanza.get_from()),
                msg.when.timestamp(),
                str(msg.stanza),
                muc_jid,
            ),
        )
        self.con.commit()

    def mam_launch_cleanup_task(self, loop: AbstractEventLoop):
        self.__mam_cleanup_task = loop.create_task(self.__mam_cleanup())

    async def __mam_cleanup(self):
        await sleep(6 * 24 * 3600)
        self.mam_cleanup()

    def mam_cleanup(self):
        self.cur.execute(
            "DELETE FROM mam_message WHERE sent_on < ?",
            (time() - config.MAM_MAX_DAYS * 24 * 3600,),
        )
        self.con.commit()

    def __mam_get_sent_on(self, muc_jid: str, mid: str):
        res = self.cur.execute(
            "SELECT sent_on "
            "FROM mam_message "
            "WHERE message_id = ? "
            "AND muc_id = (SELECT id FROM muc WHERE jid = ?)",
            (mid, muc_jid),
        )
        row = res.fetchone()
        if row is None:
            raise XMPPError("item-not-found", f"Message {mid} not found")
        return row[0]

    def __mam_bound(
        self,
        muc_jid: str,
        date: Optional[datetime] = None,
        id_: Optional[str] = None,
        comparator=min,
    ):
        if id_ is not None:
            after_id_sent_on = self.__mam_get_sent_on(muc_jid, id_)
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
        muc_jid: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        before_id: Optional[str] = None,
        after_id: Optional[str] = None,
        ids: Collection[str] = (),
        last_page_n: Optional[int] = None,
        sender: Optional[str] = None,
        flip=False,
    ):
        query = (
            "SELECT xml, sent_on FROM mam_message "
            "WHERE muc_id = (SELECT id FROM muc WHERE jid = ?)"
        )
        params: list[Union[str, float, int]] = [muc_jid]

        if start_date or after_id:
            subquery, timestamp = self.__mam_bound(muc_jid, start_date, after_id, max)
            query += subquery
            params.append(timestamp)
        if end_date or before_id:
            subquery, timestamp = self.__mam_bound(muc_jid, end_date, before_id, min)
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

        res = self.cur.execute(query, params)

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

    def mam_get_first_and_last(self, muc_jid: str):
        res = self.cur.execute(
            "SELECT message_id, sent_on "
            "FROM mam_message "
            "JOIN muc ON muc.jid = ? "
            "WHERE sent_on = (SELECT MIN(sent_on) FROM mam_message WHERE muc_id = muc.id) "
            "   OR sent_on = (SELECT MAX(sent_on) FROM mam_message WHERE muc_id = muc.id) "
            " ORDER BY sent_on",
            (muc_jid,),
        )
        return res.fetchall()

    def attachment_remove(self, legacy_id):
        self.cur.execute("DELETE FROM attachment WHERE legacy_id = ?", legacy_id)
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
        extra_value: str,
        extra_key="session_jid",
        sql: Optional[TemporaryDB] = None,
        create_table=False,
        is_inverse=False,
    ):
        if sql is None:
            sql = db
        self.db = sql
        self.table = table
        self.key1 = key1
        self.key2 = key2
        self.extra_key = extra_key
        self.extra_value = extra_value
        if create_table:
            sql.cur.execute(
                f"CREATE TABLE {table} (id "
                "INTEGER PRIMARY KEY, "
                f"{extra_key} TEXT, "
                f"{key1} UNIQUE, "
                f"{key2} UNIQUE)",
            )
        if is_inverse:
            return
        self.inverse = SQLBiDict[ValueType, KeyType](
            table, key2, key1, extra_value, sql=sql, is_inverse=True
        )

    def __setitem__(self, key: KeyType, value: ValueType):
        self.db.cur.execute(
            f"REPLACE INTO {self.table}"
            f"({self.extra_key}, {self.key1}, {self.key2}) "
            "VALUES (?, ?, ?)",
            (self.extra_value, key, value),
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
            f"WHERE {self.key1} = ? AND {self.extra_key} = ?",
            (item, self.extra_value),
        ).fetchone()
        return res is not None

    @lru_cache(100)
    def get(self, item: KeyType) -> Optional[ValueType]:
        res = self.db.cur.execute(
            f"SELECT {self.key2} FROM {self.table} "
            f"WHERE {self.key1} = ? AND {self.extra_key} = ?",
            (item, self.extra_value),
        ).fetchone()
        if res is None:
            return res
        return res[0]


db = TemporaryDB()
