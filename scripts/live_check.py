"""Проверка портала по HTTP на запущенном сервере.

Запуск (сервер уже поднят, например `python manage.py runserver 127.0.0.1:8010`):

    python3 scripts/live_check.py [base_url] [login] [password]

Скрипт проходит путь реального пользователя: получает страницу входа с CSRF-токеном,
логинится, обходит разделы и проверяет ключевые элементы разметки и раздачу статики.
"""
from __future__ import annotations

import http.cookiejar
import re
import sys
import uuid
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010"
LOGIN = sys.argv[2] if len(sys.argv) > 2 else "portal.admin"
PASSWORD = sys.argv[3] if len(sys.argv) > 3 else "PortalAdmin#2026"

class NoRedirect(urllib.request.HTTPRedirectHandler):
    """POST-запросы не должны сами уходить по редиректу — иначе не видно результата."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


jar = http.cookiejar.CookieJar()
cookie_handler = urllib.request.HTTPCookieProcessor(jar)
opener = urllib.request.build_opener(cookie_handler)
post_opener = urllib.request.build_opener(cookie_handler, NoRedirect)
opener.addheaders = [("User-Agent", "ecros-portal-check/1.0")]

failures: list[str] = []


def _url(path: str) -> str:
    """Кодирует кириллицу в пути и query-string."""
    path = urllib.parse.quote(path, safe="/?=&")
    return BASE + path


def _multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    """Собирает тело multipart/form-data вручную — без внешних зависимостей."""
    boundary = "----ecros" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode()
        )
    for name, (filename, content, content_type) in files.items():
        chunks.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'
            ).encode()
        )
        chunks.append(content)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def request(method: str, path: str, data: dict | None = None, files: dict | None = None, raw: bool = False):
    """Запрос с CSRF-токеном; files — словарь {имя поля: (имя файла, байты, тип)}."""
    token = next((c.value for c in jar if c.name == "csrftoken"), None)
    payload = None
    content_type = None
    if files:
        fields = dict(data or {})
        if token:
            fields["csrfmiddlewaretoken"] = token
        payload, content_type = _multipart(fields, files)
    elif data is not None:
        fields = dict(data)
        if token:
            fields["csrfmiddlewaretoken"] = token
        payload = urllib.parse.urlencode(fields).encode()
        content_type = "application/x-www-form-urlencoded"

    req = urllib.request.Request(_url(path), data=payload, method=method)
    req.add_header("Referer", _url(path))
    if content_type:
        req.add_header("Content-Type", content_type)
    active = post_opener if method != "GET" else opener
    try:
        response = active.open(req, timeout=20)
        body = response.read()
        return (
            response.status,
            body if raw else body.decode("utf-8", "replace"),
            response.headers,
        )
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return exc.code, body if raw else body.decode("utf-8", "replace"), exc.headers
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{method} {path}: {type(exc).__name__} {exc}")
        return 0, b"" if raw else "", {}


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
    status, login_result = post("/login/", {"username": LOGIN, "password": PASSWORD, "next": "/"}, expect=302)
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

    print("\n== вложения заявок ==")
    status, body, headers = request(
        "POST",
        "/tickets/new/",
        data={
            "category": "it",
            "priority": "normal",
            "subject": "Проверка вложений",
            "description": "Заявка создана автоматической проверкой портала",
        },
        files={"attachments": ("смета.txt", b"payload-123", "text/plain")},
    )
    created_ok = status == 302 and "/tickets/" in (headers.get("Location") or "")
    print(f"  {'✓' if created_ok else '✗'} POST /tickets/new/ с файлом            {status:3} → {headers.get('Location', '')}")
    if not created_ok:
        failures.append(f"заявка с вложением не создана ({status})")

    ticket_path = (headers.get("Location") or "").replace(BASE, "")
    page = ""
    if ticket_path:
        _, page, _ = request("GET", ticket_path)
        has_file = "смета.txt" in page and "/download/" in page
        print(f"  {'✓' if has_file else '✗'} файл виден в карточке заявки")
        if not has_file:
            failures.append("вложение не отображается в карточке заявки")

    match = re.search(r'/tickets/attachments/(\d+)/download/', page)
    if match:
        status, content, dl_headers = request(
            "GET", f"/tickets/attachments/{match.group(1)}/download/", raw=True
        )
        disposition = dl_headers.get("Content-Disposition", "")
        served_ok = status == 200 and content == b"payload-123" and "attachment" in disposition
        print(f"  {'✓' if served_ok else '✗'} GET  /tickets/attachments/{match.group(1)}/download/  {status:3}  {len(content)} байт")
        if not served_ok:
            failures.append(f"скачивание вложения вернуло {status}")
    else:
        failures.append("ссылка на скачивание вложения не найдена")

    # повторная загрузка в существующую заявку
    if ticket_path:
        status, _, _ = request(
            "POST",
            ticket_path.rstrip("/") + "/attach/",
            files={"files": ("скрин.png", b"\x89PNG-fake", "image/png")},
        )
        attach_ok = status == 302
        print(f"  {'✓' if attach_ok else '✗'} POST {ticket_path.rstrip('/')}/attach/      {status:3}")
        if not attach_ok:
            failures.append(f"дозагрузка файла вернула {status}")

    # аноним не должен скачивать
    if match:
        anonymous = http.cookiejar.CookieJar()
        # без следования редиректам: иначе не отличить «302 на форму входа» от «200 с файлом»
        anon = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(anonymous), NoRedirect
        )
        try:
            anon.open(BASE + f"/tickets/attachments/{match.group(1)}/download/", timeout=15)
            anon_status = 200
        except urllib.error.HTTPError as exc:
            anon_status = exc.code
        except Exception:  # noqa: BLE001
            anon_status = 0
        anon_ok = anon_status in (302, 403, 404)
        print(f"  {'✓' if anon_ok else '✗'} анонимный доступ к файлу закрыт       {anon_status:3}")
        if not anon_ok:
            failures.append("файл доступен без авторизации")

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
