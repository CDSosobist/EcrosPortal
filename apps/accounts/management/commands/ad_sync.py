"""Команда синхронизации портала с Active Directory.

Примеры:
    python manage.py ad_sync --dry-run
    python manage.py ad_sync --incremental
    python manage.py ad_sync --provider mock
"""
from __future__ import annotations

from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.ad.base import AdError
from apps.accounts.ad.factory import get_directory, provider_name
from apps.accounts.ad.sync import AdSyncService
from apps.accounts.models import AdSyncRun


class Command(BaseCommand):
    help = "Синхронизирует сотрудников, подразделения и роли из Active Directory"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Проверить без записи изменений")
        parser.add_argument("--incremental", action="store_true", help="Только изменённые записи")
        parser.add_argument(
            "--provider", default=None, help="Источник каталога: ldap или mock (по умолчанию из настроек)"
        )
        parser.add_argument(
            "--since",
            default=None,
            help="Инкрементально с даты (ISO 8601, например 2026-01-01T00:00:00)",
        )
        parser.add_argument("--quiet", action="store_true", help="Только итоговая строка")

    def handle(self, *args, **options):
        provider = options.get("provider") or provider_name()
        try:
            directory = get_directory(provider)
        except AdError as exc:
            raise CommandError(str(exc)) from exc

        mode = AdSyncRun.Mode.INCREMENTAL if options["incremental"] else AdSyncRun.Mode.FULL
        since = None
        if options.get("since"):
            try:
                since = datetime.fromisoformat(options["since"])
            except ValueError as exc:
                raise CommandError(f"Неверный формат --since: {exc}") from exc
            if timezone.is_naive(since):
                since = timezone.make_aware(since)
            mode = AdSyncRun.Mode.INCREMENTAL
        elif options["incremental"]:
            last_ok = (
                AdSyncRun.objects.filter(status=AdSyncRun.Status.SUCCESS)
                .order_by("-finished_at")
                .first()
            )
            since = (last_ok.finished_at if last_ok else timezone.now() - timedelta(days=1)) - timedelta(
                minutes=10
            )

        dry_run = options["dry_run"]
        if dry_run:
            mode = AdSyncRun.Mode.DRY_RUN

        service = AdSyncService(directory=directory, dry_run=dry_run, mode=mode)
        run = service.run(since=since)

        counters = run.counters or {}
        if not options["quiet"]:
            self.stdout.write(self.style.MIGRATE_HEADING(f"Источник: {provider} · режим: {run.get_mode_display()}"))
            self.stdout.write(f"Получено записей:      {counters.get('users_fetched', 0)}")
            self.stdout.write(f"Создано сотрудников:   {counters.get('created', 0)}")
            self.stdout.write(f"Обновлено:             {counters.get('updated', 0)}")
            self.stdout.write(f"Без изменений:         {counters.get('unchanged', 0)}")
            self.stdout.write(f"Подразделений создано: {counters.get('departments_created', 0)}")
            self.stdout.write(f"Руководителей найдено: {counters.get('managers_resolved', 0)}")
            self.stdout.write(f"Деактивировано:        {counters.get('deactivated', 0)}")
            for warning in counters.get("warnings", [])[:10]:
                self.stdout.write(self.style.WARNING(f"  ! {warning}"))
            for error in counters.get("errors", [])[:10]:
                self.stdout.write(self.style.ERROR(f"  × {error}"))

        if run.status == AdSyncRun.Status.FAILED:
            raise CommandError(f"Синхронизация не выполнена: {run.message}")

        style = self.style.WARNING if dry_run else self.style.SUCCESS
        self.stdout.write(style(f"[{run.status}] {run.message}"))
