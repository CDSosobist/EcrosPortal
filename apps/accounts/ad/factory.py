"""Фабрика источника данных каталога."""
from __future__ import annotations

from functools import lru_cache

from django.conf import settings

from .base import AdDirectory, AdError
from .ldap_provider import LdapAdProvider
from .mock_provider import MockAdProvider


def provider_name() -> str:
    return (settings.AD.get("PROVIDER") or "ldap").strip().lower()


def get_directory(name: str | None = None) -> AdDirectory:
    """Возвращает провайдер каталога по имени (по умолчанию — из настроек)."""
    resolved = (name or provider_name()).strip().lower()
    if resolved == "mock":
        return _mock_directory()
    if resolved == "ldap":
        return LdapAdProvider()
    raise AdError(f"Неизвестный источник каталога: {resolved!r} (допустимо: ldap, mock)")


@lru_cache(maxsize=1)
def _mock_directory() -> MockAdProvider:
    """Мок-каталог кешируется: генерация 400 сотрудников не должна повторяться."""
    return MockAdProvider()
