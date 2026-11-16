"""Проверка доступности каталога и настроек интеграции.

    python manage.py ad_check
"""
from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.ad.base import AdError
from apps.accounts.ad.factory import get_directory, provider_name


class Command(BaseCommand):
    help = "Проверяет подключение к Active Directory и полноту настроек AD_*"

    def handle(self, *args, **options):
        provider = provider_name()
        self.stdout.write(self.style.MIGRATE_HEADING(f"Провайдер каталога: {provider}"))

        config = settings.AD
        required = ["SERVER", "BASE_DN", "BIND_DN", "BIND_PASSWORD"]
        missing = [key for key in required if not config.get(key)]
        if provider == "ldap" and missing:
            for key in missing:
                self.stdout.write(self.style.ERROR(f"  не задано: AD_{key}"))
            raise CommandError(
                "Настройки Active Directory неполные. Заполните .env (см. .env.example)."
            )
        for key in ("SERVER", "PORT", "USE_SSL", "BASE_DN", "USER_FILTER", "USER_SEARCH_BASE"):
            self.stdout.write(f"  {key}: {config.get(key)}")

        try:
            directory = get_directory(provider)
            health = directory.healthcheck()
        except AdError as exc:
            raise CommandError(f"Ошибка подключения: {exc}") from exc

        if health.ok:
            self.stdout.write(self.style.SUCCESS(f"✓ {health.message}"))
            for key, value in health.details.items():
                self.stdout.write(f"  {key}: {value}")
            try:
                users = directory.fetch_users()
            except AdError as exc:
                raise CommandError(f"Чтение пользователей не удалось: {exc}") from exc
            self.stdout.write(f"  пользователей в выдаче: {len(users)}")
            if users:
                sample = users[0]
                self.stdout.write(
                    "  пример записи: "
                    f"{sample.username} · {sample.label} · {sample.department_name or '—'}"
                )
        else:
            self.stdout.write(self.style.ERROR(f"✗ {health.message}"))
            raise CommandError("Каталог недоступен")
