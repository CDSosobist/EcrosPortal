"""Контракты слоя интеграции с каталогом.

Провайдер каталога отвечает только за чтение и проверку пароля; все правила
маппинга в сущности портала живут в ``apps.accounts.ad.sync``. Благодаря этому
портал можно поднять на «моке» (демо/тесты) и переключить на боевую AD без
изменения кода синхронизации.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol, runtime_checkable


class AdError(Exception):
    """Ошибка взаимодействия с каталогом (недоступность, отказ в доступе, таймаут)."""


@dataclass(slots=True)
class AdUser:
    """Нормализованная запись пользователя из AD."""

    username: str
    dn: str = ""
    guid: str = ""
    email: str = ""
    first_name: str = ""
    last_name: str = ""
    middle_name: str = ""
    display_name: str = ""
    job_title: str = ""
    department_name: str = ""
    manager_username: str = ""
    manager_dn: str = ""
    phone_internal: str = ""
    phone_mobile: str = ""
    office: str = ""
    city: str = ""
    employee_no: str = ""
    hire_date: date | None = None
    is_enabled: bool = True
    groups: list[str] = field(default_factory=list)
    group_dns: list[str] = field(default_factory=list)
    when_changed: datetime | None = None

    @property
    def label(self) -> str:
        return self.display_name or f"{self.last_name} {self.first_name}".strip() or self.username


@dataclass(slots=True)
class AdGroupRecord:
    """Группа AD, встреченная при синхронизации."""

    dn: str
    name: str
    description: str = ""
    member_count: int = 0


@dataclass(slots=True)
class AdHealth:
    """Результат проверки связи с каталогом."""

    ok: bool
    message: str
    details: dict = field(default_factory=dict)


@runtime_checkable
class AdDirectory(Protocol):
    """Интерфейс источника данных о сотрудниках."""

    name: str

    def fetch_users(self, since: datetime | None = None) -> list[AdUser]:
        """Все пользователи домена (или изменённые после ``since``)."""

    def fetch_groups(self) -> list[AdGroupRecord]:
        """Группы домена для настройки соответствия ролям."""

    def authenticate(self, username: str, password: str) -> bool:
        """Проверяет доменный пароль (LDAP simple bind от имени пользователя)."""

    def fetch_user(self, username: str) -> AdUser | None:
        """Одна запись по логину — используется при первом входе в портал."""

    def healthcheck(self) -> AdHealth:
        """Проверка доступности контроллера домена."""
