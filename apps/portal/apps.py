"""Приложение «Портал»: новости, база знаний, заявки, поиск, уведомления."""
from django.apps import AppConfig


class PortalConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.portal"
    label = "portal"
    verbose_name = "Портал"

    def ready(self):
        # Регистрирует обработчик удаления файлов вложений (portal/signals.py).
        from . import signals  # noqa: F401
