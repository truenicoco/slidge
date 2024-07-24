from __future__ import annotations

import json
from typing import Union

import sqlalchemy as sa
from slixmpp import JID


class JIDType(sa.TypeDecorator[JID]):
    """
    Custom SQLAlchemy type for JIDs
    """

    impl = sa.types.TEXT
    cache_ok = True

    def process_bind_param(self, value: JID | None, dialect: sa.Dialect) -> str | None:
        if value is None:
            return value
        return str(value)

    def process_result_value(
        self, value: str | None, dialect: sa.Dialect
    ) -> JID | None:
        if value is None:
            return value
        return JID(value)


class JSONEncodedDict(sa.TypeDecorator):
    """
    Custom SQLAlchemy type for dictionaries stored as JSON

    Note that mutations of the dictionary are not detected by SQLAlchemy,
    which is why use ``attributes.flag_modified()`` in ``UserStore.update()``
    """

    impl = sa.VARCHAR

    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            value = json.dumps(value)

        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            value = json.loads(value)
        return value


JSONSerializableTypes = Union[str, float, None, "JSONSerializable"]
JSONSerializable = dict[str, JSONSerializableTypes]


class Base(sa.orm.DeclarativeBase):
    type_annotation_map = {JSONSerializable: JSONEncodedDict, JID: JIDType}
    naming_convention = {
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_`%(constraint_name)s`",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }


def get_engine(path: str) -> sa.Engine:
    engine = sa.create_engine(path)
    return engine
