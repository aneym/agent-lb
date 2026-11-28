from __future__ import annotations

from sqlalchemy.orm.instrumentation import manager_of_class

from app.db.models import Account, AdditionalUsageHistory, UsageHistory

_ACCOUNT_MANAGER = manager_of_class(Account)
_ACCOUNT_COLUMN_NAMES = tuple(column.name for column in Account.__table__.columns)
_USAGE_HISTORY_MANAGER = manager_of_class(UsageHistory)
_USAGE_HISTORY_COLUMN_NAMES = tuple(column.name for column in UsageHistory.__table__.columns)
_ADDITIONAL_USAGE_HISTORY_MANAGER = manager_of_class(AdditionalUsageHistory)
_ADDITIONAL_USAGE_HISTORY_COLUMN_NAMES = tuple(column.name for column in AdditionalUsageHistory.__table__.columns)


def clone_account(account: Account) -> Account:
    obj: Account = _ACCOUNT_MANAGER.new_instance()
    dest = obj.__dict__
    src = account.__dict__
    for name in _ACCOUNT_COLUMN_NAMES:
        dest[name] = src[name] if name in src else getattr(account, name)
    return obj


def clone_usage_history(entry: UsageHistory | AdditionalUsageHistory) -> UsageHistory | AdditionalUsageHistory:
    if isinstance(entry, AdditionalUsageHistory):
        obj: AdditionalUsageHistory = _ADDITIONAL_USAGE_HISTORY_MANAGER.new_instance()
        cols = _ADDITIONAL_USAGE_HISTORY_COLUMN_NAMES
    else:
        obj = _USAGE_HISTORY_MANAGER.new_instance()
        cols = _USAGE_HISTORY_COLUMN_NAMES
    dest = obj.__dict__
    src = entry.__dict__
    for name in cols:
        dest[name] = src[name] if name in src else getattr(entry, name)
    return obj


def clone_standard_usage_history(entry: UsageHistory) -> UsageHistory:
    obj: UsageHistory = _USAGE_HISTORY_MANAGER.new_instance()
    dest = obj.__dict__
    src = entry.__dict__
    for name in _USAGE_HISTORY_COLUMN_NAMES:
        dest[name] = src[name] if name in src else getattr(entry, name)
    return obj
