"""Аутентификация: доменный пароль проверяется в AD, локальный — резерв.

Доменный вход работает как SSO-подобная схема без Kerberos: пользователь
вводит логин и пароль домена, портал делает simple bind к контроллеру домена.
Совпал bind — сотрудник авторизован. Пароль в портале не хранится.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth.backends import BaseBackend, ModelBackend

from .ad.base import AdError
from .ad.factory import get_directory
from .ad.sync import AdSyncService
from .models import Employee

logger = logging.getLogger(__name__)

_last_ad_error: str = ""


def get_last_ad_error() -> str:
    return _last_ad_error


def set_last_ad_error(message: str) -> None:
    global _last_ad_error
    _last_ad_error = message


def client_ip(request) -> str | None:
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class ActiveDirectoryBackend(BaseBackend):
    """Проверяет пароль средствами контроллера домена."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        username = (username or kwargs.get(Employee.USERNAME_FIELD) or "").strip().lower()
        if not username or not password:
            return None
        if not settings.AD.get("ENABLED", True):
            return None

        employee = Employee.objects.filter(username=username).first()
        if employee is not None and employee.source == Employee.Source.LOCAL:
            # служебная учётная запись — проверяется локальным бэкендом
            return None

        directory = get_directory()
        try:
            accepted = directory.authenticate(username, password)
        except AdError as exc:
            logger.warning("AD недоступен при входе %s: %s", username, exc)
            set_last_ad_error(
                "Контроллер домена недоступен. Проверьте сеть или войдите служебной учётной записью."
            )
            return None
        except Exception as exc:  # noqa: BLE001
            logger.exception("Сбой проверки пароля в AD для %s", username)
            set_last_ad_error(f"Сбой проверки пароля в домене: {exc}")
            return None

        if not accepted:
            return None

        if employee is None:
            employee = self._provision(username, directory)
            if employee is None:
                return None

        if not employee.is_active:
            set_last_ad_error(
                "Учётная запись отключена в портале. Обратитесь в отдел кадров."
            )
            return None
        if employee.missing_in_ad:
            set_last_ad_error(
                "Учётная запись не найдена в Active Directory. Обратитесь в ИТ-отдел."
            )
            return None

        employee.touch_last_login(client_ip(request))
        set_last_ad_error("")
        return employee

    def _provision(self, username: str, directory) -> Employee | None:
        """Первый вход: забираем карточку из AD и создаём учётную запись."""
        try:
            ad_user = directory.fetch_user(username)
        except AdError as exc:
            set_last_ad_error(str(exc))
            return None
        if ad_user is None or not ad_user.is_enabled:
            return None
        service = AdSyncService(directory=directory)
        employee = service.sync_single_user(ad_user, create=True)
        if employee is None:
            set_last_ad_error(
                "Не удалось создать учётную запись в портале. Обратитесь в ИТ-отдел."
            )
            return None
        logger.info("Учётная запись %s создана автоматически при входе", username)
        return employee

    def get_user(self, user_id):
        try:
            return Employee.objects.get(pk=user_id)
        except Employee.DoesNotExist:
            return None


class LocalPasswordBackend(ModelBackend):
    """Резервный вход по локальному паролю.

    Разрешён только для служебных учётных записей (источник «локальная»), чтобы
    доменные сотрудники не могли обойти AD. Аварийный суперпользователь
    сохраняет доступ, даже если домен недоступен.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        username = (username or kwargs.get(Employee.USERNAME_FIELD) or "").strip().lower()
        if not username or not password:
            return None
        employee = Employee.objects.filter(username=username).first()
        if employee is None:
            return None
        if not employee.has_usable_password():
            return None
        if employee.source == Employee.Source.AD and not employee.is_superuser:
            return None
        if not settings.ALLOW_LOCAL_PASSWORD_LOGIN and not employee.is_superuser:
            return None
        if not employee.check_password(password):
            return None
        if not employee.is_active:
            return None
        return employee
