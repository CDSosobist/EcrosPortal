"""Проверка портала по HTTP на запущенном сервере.

Запуск (сервер уже поднят, например `python manage.py runserver 127.0.0.1:8010`):

    python3 scripts/live_check.py [base_url] [login] [password]

Скрипт проходит путь реального пользователя: получает страницу входа с CSRF-токеном,
логинится, обходит разделы и проверяет ключевые элементы разметки и раздачу статики.
"""
from __future__ import annotations

import http.cookiejar
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010"
LOGIN = sys.argv[2] if len(sys.argv) > 2 else "portal.admin"
PASSWORD = sys.argv[3] if len(sys.argv) > 3 else "PortalAdmin#2026"

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
opener.addheaders = [("User-Agent", "ecros-portal-check/1.0")]

failures: list[str] = []


def _url(path: str) -> str:
    """Кодирует кириллицу в пути и query-string."""
    path = urllib.parse.quote(path, safe="/?=&")
    return BASE + path


def request(method: str, path: str, data: dict | None = None):
    token = next((c.value for c in jar if c.name == "csrftoken"), None)
    payload = None
    if data is not None:
        payload = dict(data)
        if token:
            payload["csrfmiddlewaretoken"] = token
        payload = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(_url(path), data=payload, method=method)
    req.add_header("Referer", _url(path))
    if payload is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        response = opener.open(req, timeout=20)
        return response.status, response.read().decode("utf-8", "replace"), response.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace"), exc.headers
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{method} {path}: {type(exc).__name__} {exc}")
        return 0, "", {}


def get(path: str, expect: int = 200, contains: str | None = None) -> str:
    status, body, _ = request("GET", path)
    ok = status == expect
    if ok and contains and contains not in body:
        ok = False
    if not ok:
        failures.append(f"GET {path} → {status} (ожидалось {expect}{f', «{contains}» внутри' if contains else ''})")
    print(f"  {'✓' if ok else '✗'} GET  {path:46} {status:3}  {len(body):>6} байт")
    return body


def post(path: str, data: dict, expect: int = 302) -> tuple[int, str]:
    status, body, headers = request("POST", path, data)
    ok = status == expect
    if not ok:
        failures.append(f"POST {path} → {status} (ожидалось {expect})")
    print(f"  {'✓' if ok else '✗'} POST {path:46} {status:3}  → {headers.get('Location', '')}")
    return status, body


def main() -> int:
    print(f"Портал: {BASE}")
    print("\n== публичная часть ==")
    login_html = get("/login/", contains="Вход в портал")
    get("/static/css/app.css")
    get("/static/js/app.js")
    get("/healthz/", contains="ok")
    # анонимный запрос к главной уводится на форму входа
    guest = get("/", contains="Вход в портал")
    print(f"       (аноним видит форму входа: {'да' if 'Вход в портал' in guest else 'нет'})")

    print("\n== вход ==")
    status, login_result = post("/login/", {"username": LOGIN, "password": PASSWORD, "next": "/"}, expect=200)
    if "Здравствуйте" not in login_result:
        print("       предупреждение: ответ на вход не содержит приветствие, проверяем главную отдельно")
    dashboard = get("/", contains="Здравствуйте")
    if "Здравствуйте" not in dashboard:
        print("\nВход не выполнен — дальше проверять нечего.")
        return 1

    print("\n== разделы портала ==")
    for path, needle in [
        ("/directory/", "Справочник сотрудников"),
        ("/directory/?q=Иван", "Сотрудники"),
        ("/org/", "Оргструктура"),
        ("/news/", "Новости"),
        ("/knowledge/", "База знаний"),
        ("/tickets/", "Заявки"),
        ("/notifications/", "Уведомления"),
        ("/search/?q=отпуск", "Поиск"),
        ("/profile/", "Мой профиль"),
        ("/vacations/", "Мои отпуска"),
        ("/vacations/new/", "Заявление на отпуск"),
        ("/vacations/approvals/", "Согласование"),
        ("/vacations/schedule/", "График отпусков"),
        ("/vacations/calendar/", "Календарь"),
        ("/vacations/team/", "Моя команда"),
        ("/vacations/requests/", "Заявления"),
        ("/vacations/manage/", "Настройки"),
        ("/vacations/manage/periods/", "Периоды"),
        ("/vacations/manage/chains/", "Маршруты"),
        ("/vacations/manage/balances/", "Права на отпуск"),
        ("/vacations/manage/types/", "Виды отпусков"),
        ("/vacations/manage/holidays/", "Производственный календарь"),
        ("/manage/sync/", "Синхронизация"),
        ("/manage/roles/", "Роли"),
        ("/admin/", "Управление порталом"),
    ]:
        get(path, contains=needle)

    print("\n== выгрузка графика ==")
    status, csv_body, headers = request("GET", "/vacations/schedule/2027/export/")
    content_type = headers.get("Content-Type", "")
    ok = status == 200 and "text/csv" in content_type and "Подразделение" in csv_body
    if not ok:
        failures.append(f"экспорт CSV → {status}, {content_type}")
    print(f"  {'✓' if ok else '✗'} GET  /vacations/schedule/2027/export/           {status:3}  {len(csv_body):>6} байт")

    print("\n== итог ==")
    if failures:
        for failure in failures:
            print(f"  ! {failure}")
        print(f"Проблем: {len(failures)}")
        return 1
    print("  Все проверки пройдены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
