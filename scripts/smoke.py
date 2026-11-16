"""Дымовая проверка страниц портала.

Запуск (из корня проекта, с активированным окружением Django):

    python manage.py shell -c "exec(open('scripts/smoke.py').read())"

Скрипт входит под администратором и под рядовым сотрудником, обходит все
основные маршруты и печатает коды ответов. Нужен как быстрая проверка после
изменений в шаблонах, вьюхах и правах доступа.
"""
from __future__ import annotations

import sys

from django.conf import settings
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Employee
from apps.portal.models import KnowledgeArticle, NewsPost, ServiceTicket
from apps.vacations.models import (
    ApprovalChainTemplate,
    SchedulePeriod,
    VacationRequest,
)

if "testserver" not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ["testserver"]

failures: list[str] = []


def check(client: Client, label: str, url: str, expect: tuple[int, ...] = (200,)) -> None:
    try:
        response = client.get(url, follow=False)
        status = response.status_code
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{label} {url} → исключение {type(exc).__name__}: {exc}")
        print(f"  ✗ {label:10} {url:52} ИСКЛЮЧЕНИЕ {type(exc).__name__}: {exc}")
        return
    mark = "✓" if status in expect else "✗"
    if status not in expect:
        failures.append(f"{label} {url} → {status} (ожидалось {expect})")
    print(f"  {mark} {label:10} {url:52} {status}")


def run() -> int:
    period = SchedulePeriod.objects.order_by("-year").first()
    request_obj = VacationRequest.objects.filter(status="approved").first()
    pending = VacationRequest.objects.filter(status="pending").first()
    employee = Employee.objects.filter(is_active=True, department__isnull=False).first()
    chain = ApprovalChainTemplate.objects.first()
    news = NewsPost.objects.first()
    article = KnowledgeArticle.objects.first()
    ticket = ServiceTicket.objects.first()

    print("\n== Гость ==")
    guest = Client()
    check(guest, "гость", reverse("accounts:login"))
    check(guest, "гость", reverse("portal:dashboard"), expect=(302,))
    check(guest, "гость", "/healthz/")

    print("\n== Администратор ==")
    admin = Client()
    admin_user = Employee.objects.filter(username="portal.admin").first()
    if admin_user is None:
        print("  ! нет portal.admin — выполните seed_demo")
        return 1
    admin.force_login(admin_user)
    for name in (
        "portal:dashboard",
        "portal:directory",
        "portal:org_chart",
        "portal:news_list",
        "portal:kb_list",
        "portal:tickets",
        "portal:ticket_new",
        "portal:notifications",
        "portal:search",
        "accounts:profile",
        "accounts:manage_sync",
        "accounts:manage_roles",
        "vacations:index",
        "vacations:request_new",
        "vacations:request_list",
        "vacations:approvals",
        "vacations:schedule",
        "vacations:calendar",
        "vacations:team",
        "vacations:manage",
        "vacations:manage_periods",
        "vacations:manage_balances",
        "vacations:manage_chains",
        "vacations:chain_new",
        "vacations:manage_types",
        "vacations:manage_holidays",
    ):
        check(admin, "админ", reverse(name))
    if period:
        check(admin, "админ", reverse("vacations:schedule_year", args=[period.year]))
        check(admin, "админ", reverse("vacations:schedule_export", args=[period.year]))
        check(admin, "админ", f"{reverse('vacations:manage_balances')}?year={period.year}")
        check(admin, "админ", f"{reverse('vacations:manage_holidays')}?year={period.year}")
    if employee:
        check(admin, "админ", reverse("portal:employee_detail", args=[employee.pk]))
    if request_obj:
        check(admin, "админ", reverse("vacations:request_detail", args=[request_obj.pk]))
        check(admin, "админ", reverse("vacations:request_edit", args=[request_obj.pk]), expect=(302,))
    if pending:
        check(admin, "админ", reverse("vacations:request_detail", args=[pending.pk]))
    if chain:
        check(admin, "админ", reverse("vacations:chain_edit", args=[chain.pk]))
    if news:
        check(admin, "админ", news.get_absolute_url())
    if article:
        check(admin, "админ", article.get_absolute_url())
    if ticket:
        check(admin, "админ", ticket.get_absolute_url())
    check(admin, "админ", "/admin/")
    check(admin, "админ", reverse("portal:kb_list") + "?q=отпуск")
    check(admin, "админ", reverse("portal:tickets") + "?status=new")
    check(admin, "админ", reverse("vacations:request_list") + "?status=pending")
    check(admin, "админ", reverse("vacations:calendar") + "?month=1&year=2027")

    print("\n== Рядовой сотрудник ==")
    staff = Client()
    plain = (
        Employee.objects.filter(is_active=True, department__isnull=False, manager__isnull=False)
        .exclude(is_staff=True)
        .first()
    )
    if plain is not None:
        staff.force_login(plain)
        for name in (
            "portal:dashboard",
            "portal:directory",
            "portal:tickets",
            "vacations:index",
            "vacations:request_new",
            "vacations:approvals",
            "vacations:schedule",
            "vacations:calendar",
        ):
            check(staff, "сотрудник", reverse(name))
        check(staff, "сотрудник", reverse("vacations:manage"), expect=(403,))
        check(staff, "сотрудник", reverse("accounts:manage_sync"), expect=(403,))
        check(staff, "сотрудник", reverse("accounts:manage_roles"), expect=(403,))
        check(staff, "сотрудник", reverse("portal:news_new"), expect=(404,))

    print("\n== Ошибки ==")
    if failures:
        for failure in failures:
            print(f"  ! {failure}")
        print(f"\nИтого проблем: {len(failures)}")
        return 1
    print("  Проблем нет, все страницы отвечают ожидаемо.")
    return 0


sys.exit(run())
