from __future__ import annotations

from enum import Enum

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def enum_values(enum_cls: type[Enum]) -> list[str]:
    return [str(member.value) for member in enum_cls]
