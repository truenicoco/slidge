import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from time import time
from typing import TYPE_CHECKING, Collection, Optional, Union

from slixmpp.exceptions import XMPPError

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

    def mam_clean_history(self, muc_jid: str, retention_days: int):
        self.cur.execute(
            """
            DELETE FROM
                mam_message
            WHERE
                muc_id = (SELECT id FROM muc WHERE jid = ?)
                AND sent_on < ?
            """,
            (muc_jid, time() - retention_days * 24 * 3600),
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
            query += f" message_id IN ({','.join('?' * len(ids))})"
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
        while row := res.fetchone():
            yield row


db = TemporaryDB()
