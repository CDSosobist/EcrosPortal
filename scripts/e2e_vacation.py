"""Сквозная проверка графика отпусков через реальный HTTP.

Запуск (сервер уже работает на 127.0.0.1:8010):

    PYTHONPATH=. python manage.py shell -c "exec(open('scripts/e2e_vacation.py').read())"

Скрипт проходит полный путь пользователей так, как это делают люди в браузере:
сотрудник создаёт заявление → руководитель согласует → кадры согласуют →
заявление попадает в график. Затем проверяется ветка отказа.
CSRF-токены используются настоящие, поэтому проверяется и работа форм.
"""
from __future__ import annotations

import http.cookiejar
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

from apps.accounts.models import Employee, Role
from apps.vacations.models import SchedulePeriod, VacationRequest, VacationType

BASE = "http://127.0.0.1:8010"
PASSWORD = "demo1234"  # пароль демо-каталога AD
problems: list[str] = []


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Не следуем за редиректом формы — иначе не увидеть результат POST."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


class Session:
    def __init__(self, name: str):
        self.name = name
        self.jar = http.cookiejar.CookieJar()
        self.cookie_handler = urllib.request.HTTPCookieProcessor(self.jar)
        self.opener = urllib.request.build_opener(self.cookie_handler)
        self.post_opener = urllib.request.build_opener(self.cookie_handler, NoRedirect)
        self.opener.addheaders = [("User-Agent", "ecros-e2e/1.0"), ("Accept-Language", "ru")]

    def _token(self) -> str:
        return next((c.value for c in self.jar if c.name == "csrftoken"), "")

    def get(self, path: str) -> tuple[int, str]:
        try:
            response = self.opener.open(BASE + path, timeout=30)
            return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

    def post(self, path: str, data: dict) -> tuple[int, str, str]:
        payload = dict(data)
        payload["csrfmiddlewaretoken"] = self._token()
        request = urllib.request.Request(
            BASE + path, data=urllib.parse.urlencode(payload).encode()
        )
        request.add_header("Referer", BASE + path)
        try:
            response = self.post_opener.open(request, timeout=30)
            return (
                response.status,
                response.headers.get("Location", ""),
                response.read().decode("utf-8", "replace"),
            )
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("Location", ""), exc.read().decode("utf-8", "replace")

    def login(self) -> bool:
        self.get("/login/")
        self.post("/login/", {"username": self.name, "password": PASSWORD, "next": "/"})
        status, body = self.get("/")
        return status == 200 and "Здравствуйте" in body


def check(condition: bool, message: str) -> None:
    print(f"  {'✓' if condition else '✗'} {message}")
    if not condition:
        problems.append(message)


def pick_worker() -> tuple[Employee, Employee, Employee] | None:
    """Находит сотрудника с живой цепочкой «руководитель → кадры»."""
    period = SchedulePeriod.objects.filter(year=max(SchedulePeriod.objects.values_list("year", flat=True))).first()
    if period is None:
        return None
    hr_role = Role.objects.filter(code="hr").first()
    hr = hr_role.head if hr_role and hr_role.head else (hr_role.members.first() if hr_role else None)
    if hr is None:
        return None
    for employee in Employee.objects.filter(
        is_active=True, department__isnull=False, manager__isnull=False
    ).select_related("manager")[:200]:
        manager = employee.manager
        if manager is None or not manager.is_active or manager.pk == employee.pk:
            continue
        if manager.pk == hr.pk:
            continue
        if employee.has_role("hr", "admin"):
            continue
        return employee, manager, hr
    return None


def free_window(employee: Employee, year: int, length: int = 10) -> tuple[date, date] | None:
    """Подбирает свободные даты отпуска в году."""
    busy = [
        (r.start_date, r.end_date)
        for r in VacationRequest.objects.filter(
            employee=employee,
            status__in=[VacationRequest.Status.APPROVED, VacationRequest.Status.PENDING],
        )
    ]
    cursor = date(year, 1, 15)
    limit = date(year, 12, 15)
    while cursor + timedelta(days=length) <= limit:
        end = cursor + timedelta(days=length - 1)
        if not any(not (end < s or cursor > e) for s, e in busy):
            return cursor, end
        cursor += timedelta(days=length)
    return None


def main() -> int:
    picked = pick_worker()
    if picked is None:
        print("Не удалось подобрать сотрудника с цепочкой согласования — проверьте seed_demo.")
        return 1
    worker, manager, hr = picked
    print(f"Заявитель: {worker.full_name} ({worker.username})")
    print(f"Руководитель: {manager.full_name} ({manager.username})")
    print(f"Кадры: {hr.full_name} ({hr.username})")

    open_list = [p for p in SchedulePeriod.objects.order_by("-year") if p.accepts_requests]
    period = open_list[0] if open_list else SchedulePeriod.objects.order_by("-year").first()
    annual = VacationType.objects.filter(code="annual").first() or VacationType.objects.first()
    window = free_window(worker, period.year)
    if window is None:
        print(f"Нет свободных дат в {period.year} для заявителя.")
        return 1
    start, end = window
    print(f"Период отпуска: {start:%d.%m.%Y}–{end:%d.%m.%Y}")

    print("\n== 1. Сотрудник создаёт заявление ==")
    worker_session = Session(worker.username)
    check(worker_session.login(), "вход сотрудника по доменному паролю")
    status, form_html = worker_session.get("/vacations/new/")
    check(status == 200 and "Заявление на отпуск" in form_html, "форма заявления открывается")
    check("Маршрут согласования" in form_html, "на форме показан маршрут согласования")

    status, location, body = worker_session.post(
        "/vacations/new/",
        {
            "period": period.pk,
            "vacation_type": annual.pk,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "employee_comment": "Сквозной тест: создание через веб-интерфейс",
        },
    )
    check(status == 302 and "/vacations/requests/" in location, f"заявление создано ({status} → {location})")
    match = re.search(r"/vacations/requests/(\d+)/", location or "")
    if not match:
        print("  Не удалось определить номер заявления, дальше проверять нечего.")
        return 1
    request_obj = VacationRequest.objects.get(pk=int(match.group(1)))
    check(request_obj.status == VacationRequest.Status.PENDING, "статус: на согласовании")
    check(request_obj.steps.count() >= 2, f"в маршруте {request_obj.steps.count()} шага(ов)")

    status, detail_html = worker_session.get(f"/vacations/requests/{request_obj.pk}/")
    check(status == 200 and "Маршрут согласования" in detail_html, "карточка заявления открывается")
    check("На согласовании" in detail_html, "в карточке отображается статус")

    print("\n== 2. Руководитель согласует первый шаг ==")
    manager_session = Session(manager.username)
    check(manager_session.login(), "вход руководителя")
    status, inbox = manager_session.get("/vacations/approvals/")
    check(status == 200 and request_obj.employee.short_name in inbox, "заявление в очереди руководителя")
    first_step = request_obj.current_step
    check(first_step is not None and first_step.approver_id == manager.pk, "первый шаг назначен руководителю")
    status, location, _ = manager_session.post(
        f"/vacations/steps/{first_step.pk}/approve/", {"comment": "Согласовано, даты подходят"}
    )
    check(status == 302, f"решение по шагу принято ({status})")
    request_obj.refresh_from_db()
    check(
        request_obj.steps.get(pk=first_step.pk).status == "approved",
        "шаг руководителя согласован",
    )
    check(request_obj.status == VacationRequest.Status.PENDING, "заявление ушло на следующий шаг")

    print("\n== 3. Кадры согласуют второй шаг ==")
    hr_session = Session(hr.username)
    check(hr_session.login(), "вход сотрудника отдела кадров")
    second_step = request_obj.current_step
    check(second_step is not None, "есть активный шаг для кадров")
    if second_step is not None:
        check(second_step.approver_id == hr.pk, f"шаг назначен кадровику ({second_step.approver.short_name})")
        status, _, _ = hr_session.post(
            f"/vacations/steps/{second_step.pk}/approve/", {"comment": "Проверено, дни списаны"}
        )
        check(status == 302, f"решение кадров принято ({status})")
    request_obj.refresh_from_db()
    check(request_obj.status == VacationRequest.Status.APPROVED, "заявление согласовано полностью")
    check(request_obj.decided_at is not None, "зафиксирована дата решения")

    print("\n== 4. Заявление попало в график ==")
    status, schedule_html = hr_session.get(f"/vacations/schedule/{period.year}/")
    check(status == 200, "график открывается")
    check(worker.short_name in schedule_html, "сотрудник виден в графике")
    status, csv_body = hr_session.get(f"/vacations/schedule/{period.year}/export/")
    check(
        status == 200 and worker.last_name in csv_body,
        "согласованный отпуск есть в выгрузке графика",
    )

    print("\n== 5. Ветка отказа ==")
    second_window = free_window(worker, period.year, length=5)
    if second_window is None:
        print("  (нет свободных дат для второго заявления — пропуск)")
    else:
        start2, end2 = second_window
        status, location, _ = worker_session.post(
            "/vacations/new/",
            {
                "period": period.pk,
                "vacation_type": annual.pk,
                "start_date": start2.isoformat(),
                "end_date": end2.isoformat(),
                "employee_comment": "Второе заявление для проверки отказа",
            },
        )
        check(status == 302, "второе заявление создано")
        match = re.search(r"/vacations/requests/(\d+)/", location or "")
        if match:
            rejected_request = VacationRequest.objects.get(pk=int(match.group(1)))
            step = rejected_request.current_step
            check(step is not None, "у второго заявления есть активный шаг")
            if step is not None:
                status, _, _ = manager_session.post(
                    f"/vacations/steps/{step.pk}/reject/",
                    {"comment": "В эти даты не хватает сотрудников в отделе"},
                )
                check(status == 302, "отказ отправлен")
                rejected_request.refresh_from_db()
                check(rejected_request.status == VacationRequest.Status.REJECTED, "заявление отклонено")
                check(
                    "не хватает сотрудников" in rejected_request.decision_comment,
                    "причина отказа сохранена",
                )

                status, detail = worker_session.get(f"/vacations/requests/{rejected_request.pk}/")
                check(status == 200 and "Отклонено" in detail, "сотрудник видит отказ в карточке")
                check("не хватает сотрудников" in detail, "сотрудник видит причину отказа")

    print("\n== Итог ==")
    if problems:
        for problem in problems:
            print(f"  ! {problem}")
        print(f"Проблем: {len(problems)}")
        return 1
    print("  Сквозной сценарий пройден без ошибок.")
    return 0


main()
