"""Напоминания по графику отпусков.

Запускается по расписанию (cron/systemd timer), например раз в сутки:

    python manage.py vacation_reminders

Что делает:
  * напоминает согласующим о заявлениях, которые долго ждут решения;
  * предупреждает сотрудников о начале согласованного отпуска;
  * подсказывает руководителям, что остались неутверждённые заявления
    в периоде, у которого подходит срок приёма.
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from apps.portal.models import Notification

from apps.vacations.models import SchedulePeriod, VacationRequest, VacationRequestStep


class Command(BaseCommand):
    help = "Отправляет напоминания по согласованию и предстоящим отпускам"

    def add_arguments(self, parser):
        parser.add_argument("--stale-days", type=int, default=2, help="Сколько дней считать «зависшим»")
        parser.add_argument("--ahead-days", type=int, default=7, help="За сколько дней предупреждать об отпуске")
        parser.add_argument("--dry-run", action="store_true", help="Только показать, ничего не отправлять")

    def handle(self, *args, **options):
        self.dry_run = options["dry_run"]
        self.sent = 0
        self.now = timezone.now()

        self._remind_approvers(options["stale_days"])
        self._remind_employees(options["ahead_days"])
        self._remind_deadlines()

        prefix = "[dry-run] " if self.dry_run else ""
        self.stdout.write(self.style.SUCCESS(f"{prefix}Отправлено напоминаний: {self.sent}"))

    # -- вспомогательное --------------------------------------------------
    def _already_sent(self, recipient, url: str, hours: int = 20) -> bool:
        return Notification.objects.filter(
            recipient=recipient,
            url=url,
            created_at__gte=self.now - timedelta(hours=hours),
        ).exists()

    def _notify(self, recipient, *, title: str, body: str, url: str, kind: str) -> None:
        if recipient is None or self._already_sent(recipient, url):
            return
        self.sent += 1
        if self.dry_run:
            self.stdout.write(f"  → {recipient.short_name}: {title}")
            return
        Notification.push(recipient, title=title, body=body, url=url, kind=kind)

    # -- правила ----------------------------------------------------------
    def _remind_approvers(self, stale_days: int) -> None:
        threshold = self.now - timedelta(days=stale_days)
        steps = (
            VacationRequestStep.objects.filter(
                status=VacationRequestStep.Status.PENDING,
                request__status=VacationRequest.Status.PENDING,
                request__submitted_at__lte=threshold,
                approver__isnull=False,
            )
            .select_related("request", "request__employee", "approver")
        )
        for step in steps:
            request_obj = step.request
            self._notify(
                step.approver,
                title=f"Напоминание: заявление {request_obj.number} ждёт вашего решения",
                body=(
                    f"{request_obj.employee.full_name}: "
                    f"{request_obj.start_date:%d.%m.%Y}–{request_obj.end_date:%d.%m.%Y} "
                    f"({request_obj.days_count} дн.). Шаг: {step.title}."
                ),
                url=reverse("vacations:request_detail", args=[request_obj.pk]),
                kind=Notification.Kind.APPROVAL,
            )

    def _remind_employees(self, ahead_days: int) -> None:
        today = timezone.localdate()
        horizon = today + timedelta(days=ahead_days)
        requests = VacationRequest.objects.filter(
            status=VacationRequest.Status.APPROVED,
            start_date__gte=today,
            start_date__lte=horizon,
        ).select_related("employee", "vacation_type")
        for request_obj in requests:
            days_left = (request_obj.start_date - today).days
            self._notify(
                request_obj.employee,
                title=f"Скоро отпуск: {request_obj.start_date:%d.%m.%Y}",
                body=(
                    f"До начала {days_left} дн. "
                    f"({request_obj.vacation_type.name}, {request_obj.days_count} дн.). "
                    "Не забудьте передать дела коллегам."
                ),
                url=reverse("vacations:request_detail", args=[request_obj.pk]),
                kind=Notification.Kind.VACATION,
            )

    def _remind_deadlines(self) -> None:
        today = timezone.localdate()
        periods = SchedulePeriod.objects.filter(
            status__in=[SchedulePeriod.Status.COLLECTING, SchedulePeriod.Status.REVIEW],
            submission_deadline__isnull=False,
            submission_deadline__gte=today,
            submission_deadline__lte=today + timedelta(days=14),
        )
        for period in periods:
            pending = period.requests.filter(status=VacationRequest.Status.PENDING).count()
            if not pending:
                continue
            approver_ids = set(
                VacationRequestStep.objects.filter(
                    status=VacationRequestStep.Status.PENDING,
                    request__period=period,
                    approver__isnull=False,
                ).values_list("approver_id", flat=True)
            )
            from apps.accounts.models import Employee

            for employee in Employee.objects.filter(pk__in=approver_ids):
                self._notify(
                    employee,
                    title=f"График отпусков {period.year}: осталось {pending} заявлений",
                    body=(
                        f"Приём заявлений до {period.submission_deadline:%d.%m.%Y}. "
                        "Пожалуйста, завершите согласование."
                    ),
                    url=reverse("vacations:approvals"),
                    kind=Notification.Kind.APPROVAL,
                )
