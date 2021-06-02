import datetime
import sqlite3
import threading

from typing import Tuple


class MailboxDB:
    def __init__(self, db_path='mailbox.sqlite3'):
        self.db = sqlite3.connect(db_path)
        self.lock = threading.Lock()

        if not self._db_query(
                "select 1 from sqlite_master where name='message'"):
            self._db_exec(
                "create table message(id integer primary key, content varchar, recv_date timestamp, del boolean)"
            )

    def _db_query(self, sql: str, args: list = []) -> tuple:
        return self.db.execute(sql, args).fetchall()

    def _db_exec(self, sql: str, args: list = []):
        self.db.execute(sql, args)
        self.db.commit()

    def get_stat(self) -> Tuple[int]:
        raw_count = self._db_query("select count(*) from message")
        if raw_count[0][0]:
            return (raw_count[0][0],
                    self._db_query("select sum(length(content)) from message")
                    [0][0])
        else:
            return (0, 0)

    def get_message_with_id(self, msg_id: int) -> str:
        query_result = self._db_query(
            "select content, del from (select row_number() over (order by recv_date desc) as row_num, content, del from message) where row_num=?",
            [msg_id])
        if query_result:
            if query_result[0][1]:
                raise Exception("this message was deleted")
            return query_result[0][0]
        else:
            raise Exception("no such message")

    def get_message_length_list(self) -> Tuple[Tuple[int]]:
        return tuple(
            map(
                lambda i: i[:2],
                filter(
                    lambda i: not i[2],
                    self._db_query(
                        "select row_number() over (order by recv_date desc) as row_num, length(content), del from message"
                    ))))

    def get_message_length_with_id(self, msg_id: int) -> int:
        return len(self.get_message_with_id(msg_id))

    def delete_message_with_id(self, msg_id: int):
        # check if the msg exists
        self.get_message_with_id(msg_id)

        self._db_exec(
            "update message set del=1 where id in (select id from (select row_number() over (order by recv_date desc) as row_num, id from message) where row_num=?)",
            [msg_id])

    def reset_messages(self):
        self._db_exec("update message set del=0 where del=1")

    def insert_message(self, msg: str):
        self._db_exec("insert into message values(null, ?, ?, 0)",
                      [msg, datetime.datetime.now()])

    def _perform_deletion(self):
        self._db_exec("delete from message where del=1")

    def aquire(self):
        self.lock.acquire()

    def release(self):
        self._perform_deletion()
        self.lock.release()


db = MailboxDB()
