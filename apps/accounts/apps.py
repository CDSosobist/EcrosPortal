"""Приложение «Сотрудники»: оргструктура, учётные записи, роли, синхронизация с AD."""
from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"
    verbose_name = "Сотрудники и роли"
