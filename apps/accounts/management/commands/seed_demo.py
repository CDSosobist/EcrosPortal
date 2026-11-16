"""Наполнение портала демонстрационными данными.

Команда поднимает «живой» пилот за один запуск:
  * роли портала и их связь с группами AD;
  * виды отпусков, производственный календарь, периоды графиков;
  * маршруты согласования (руководитель → кадры, кадры → директор и т.д.);
  * 400 сотрудников из мок-каталога AD (или боевой AD, если настроен);
  * новости, базу знаний, полезные ссылки, заявки в поддержку;
  * заявления на отпуск в разных статусах — через настоящий движок согласования.

Запуск:  python manage.py seed_demo
Идемпотентность: повторный запуск не создаёт дубликаты справочников и контента.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.ad.base import AdError
from apps.accounts.ad.factory import get_directory, provider_name
from apps.accounts.ad.sync import AdSyncService
from apps.accounts.models import Department, Employee, Role
from apps.portal.models import (
    KbCategory,
    KnowledgeArticle,
    NewsCategory,
    NewsPost,
    QuickLink,
    ServiceTicket,
    TicketComment,
)
from apps.vacations import services as vacation_services
from apps.vacations.models import (
    ApprovalChainStep,
    ApprovalChainTemplate,
    Holiday,
    SchedulePeriod,
    VacationRequest,
    VacationType,
)

HOLIDAYS_RU = [
    ((1, 1), "Новогодние каникулы"),
    ((1, 2), "Новогодние каникулы"),
    ((1, 3), "Новогодние каникулы"),
    ((1, 4), "Новогодние каникулы"),
    ((1, 5), "Новогодние каникулы"),
    ((1, 6), "Новогодние каникулы"),
    ((1, 7), "Рождество Христово"),
    ((1, 8), "Новогодние каникулы"),
    ((2, 23), "День защитника Отечества"),
    ((3, 8), "Международный женский день"),
    ((5, 1), "Праздник Весны и Труда"),
    ((5, 9), "День Победы"),
    ((6, 12), "День России"),
    ((11, 4), "День народного единства"),
]

ROLES = [
    ("employee", "Сотрудник", ["ECROS-Portal-Users"], 10, "Базовый доступ к порталу"),
    ("manager", "Руководитель", ["ECROS-Portal-Managers"], 20, "Согласование отпусков подчинённых"),
    ("hr", "Отдел кадров", ["ECROS-Portal-HR"], 30, "Кадровые процессы, график отпусков"),
    ("director", "Директор направления", ["ECROS-Portal-Directors"], 40, "Верхний уровень согласования"),
    ("admin", "Администратор портала", ["ECROS-Portal-Admins"], 50, "Настройка портала и синхронизации"),
]

VACATION_TYPES = [
    ("annual", "Ежегодный оплачиваемый отпуск", 28, True, True, "#00875c", 10),
    ("additional", "Дополнительный оплачиваемый отпуск", 0, True, True, "#0c6d4f", 20),
    ("unpaid", "Отпуск без сохранения заработной платы", 0, False, True, "#8e979e", 30),
    ("study", "Учебный отпуск", 0, False, True, "#b16c00", 40),
]

NEWS = [
    (
        "Утверждён график отпусков: подайте заявление до 1 декабря",
        "Кадры",
        "Собрание по графику отпусков на следующий год — 15 ноября в 16:00.",
        """Уважаемые коллеги!

Отдел кадров открыл сбор заявлений в график отпусков на следующий год.

## Что нужно сделать
1. Зайдите в раздел «Отпуска» → «Новое заявление».
2. Выберите удобные даты — портал сам рассчитает календарные дни и проверит остаток.
3. Отправьте заявление: оно уйдёт руководителю, затем в отдел кадров.

## Сроки
- Заявления принимаются **до 1 декабря**.
- Согласованные отпуска автоматически попадают в сводный график компании.
- После утверждения графика перенос дат возможен только через отдел кадров.

Если остались дни с прошлого года — их можно перенести, обратившись в кадры до конца января.

> Просим планировать отпуска с учётом производственной нагрузки подразделения: если в отделе
> одновременно отсутствует более 40% сотрудников, руководитель вправе предложить другие даты.
""",
    ),
    (
        "Обновление корпоративного портала: что нового",
        "ИТ",
        "Справочник сотрудников, оргструктура, база знаний и заявки в одном окне.",
        """Внутренний портал перешёл на новую версию.

## Новые возможности
- **Справочник сотрудников** с поиском по фамилии, должности и телефону.
- **Оргструктура** с руководителями подразделений.
- **График отпусков** с маршрутами согласования: руководитель → кадры → директор направления.
- **Заявки в поддержку** для ИТ, кадров и АХО — статус и переписка сохраняются в портале.

## Вход в портал
Используется доменная учётная запись Active Directory: тот же логин и пароль, что и на рабочем компьютере.

При возникновении проблем пишите в заявку категории «ИТ и оборудование».
""",
    ),
    (
        "Плановое обслуживание серверного оборудования в выходные",
        "ИТ",
        "В субботу с 09:00 до 14:00 возможны перерывы в работе внутренних систем.",
        """В субботу с 09:00 до 14:00 ИТ-отдел проводит плановое обслуживание серверного оборудования.

## Что будет недоступно
- Файловый сервер (папки \\\\fs01\\share и \\\\fs01\\archive);
- Корпоративный портал и почта — короткие перерывы до 15 минутный;
- Система документооборота.

## Что делать
Сохраните необходимые документы заранее. Если работа критична — свяжитесь с дежурным администратором.

> Телефон дежурной смены: доб. 1201
""",
    ),
    (
        "Новый сотрудник в отделе продаж",
        "Компания",
        "Приветствуем коллегу и напоминаем о вводном курсе адаптации.",
        """В отделе продаж начал работу новый менеджер — познакомиться можно в справочнике портала.

Напоминаем: вводный курс адаптации и все необходимые инструкции доступны в
[базе знаний](/) в разделе «Онбординг». Руководителям напоминаем о необходимости
провести итоговую встречу по итогам испытательного срока — сроки указаны в карточке сотрудника.
""",
    ),
    (
        "Пожарная безопасность: учебная эвакуация 20 числа",
        "Компания",
        "В 11:00 пройдёт учебная эвакуация — просим прервать работу и покинуть помещения.",
        """В 11:00 запланирована учебная эвакуация по адресу офиса.

## Порядок действий
1. Завершить текущую операцию и сохранить документы.
2. Взять средства индивидуальной защиты, следовать за ответственным по этажу.
3. Собраться на площадке у главного входа.

Доступ в помещения будет закрыт до завершения переклички — ориентировочно 30 минут.
""",
    ),
]

KB_ARTICLES = [
    (
        "Как оформить отпуск в портале",
        "Кадры",
        "Онбординг",
        "отпуск, заявление, график, согласование",
        """## Пошаговая инструкция

1. В меню выберите **Отпуска → Новое заявление**.
2. Укажите вид отпуска и даты. Портал покажет, сколько календарных дней получилось и сколько дней осталось.
3. Проверьте маршрут согласования — он формируется автоматически по вашему подразделению.
4. Нажмите **Отправить на согласование**.

## Что дальше
- Заявление уходит непосредственному руководителю, затем в отдел кадров.
- Статус виден в разделе «Мои отпуска»; каждое решение фиксируется в истории.
- При отказе можно исправить даты и отправить заново.

## Правила расчёта
- Отпуск считается в **календарных** днях, нерабочие праздничные дни не включаются (ст. 120 ТК РФ).
- Минимум 3 дня до начала — для подразделений с сменным графиком 5 дней.
- Срочное оформление (по семейным обстоятельствам) — через отдел кадров, минуя портал.
""",
    ),
    (
        "Первый день в компании: чек-лист",
        "Онбординг",
        "Онбординг",
        "адаптация, новичок, доступы",
        """## Первый день
- Получите пропуск и пропуск в столовую у администратора этажа.
- Проверьте вход в портал и почту доменной учётной записью.
- Познакомьтесь с руководителем и командой — список в справочнике сотрудников.

## Первая неделя
- Пройдите курс «Основы безопасности» в базе знаний.
- Проверьте, что у вас есть доступ к общим папкам подразделения.
- Настройте уведомления портала по отпускам и заявкам.

## Первые 30 дней
Руководитель назначает вводные встречи и ставит цели на испытательный срок.
Итоги подводятся не позднее чем за 3 дня до окончания испытательного срока.
""",
    ),
    (
        "Доступы и учётные записи: как запросить",
        "ИТ",
        "ИТ и оборудование",
        "доступ, учётная запись, VPN, папки",
        """## Что запрашивается через портал
Заявка в разделе **Заявки → Создать заявку**, категория «Доступы и учётные записи».

Укажите:
- систему или ресурс (общая папка, 1С, VPN, ЭДО);
- уровень доступа (чтение, изменение, администрирование);
- обоснование и срок, если доступ временный.

## Сроки
- Стандартный доступ — в течение рабочего дня.
- Права администратора — до 3 рабочих дней, требуется согласование руководителя.

## Увольнение и перевод
При переводе между подразделениями права пересматриваются автоматически:
синхронизация с Active Directory обновляет группы доступа в течение часа.
""",
    ),
    (
        "Оборудование: заказ, замена, ремонт",
        "ИТ",
        "ИТ и оборудование",
        "ноутбук, оборудование, ремонт, заявка",
        """## Заказ нового оборудования
Заявка категории «ИТ и оборудование» с указанием:
- типа оборудования и характеристик;
- производственной необходимости;
- согласования руководителя (в комментарии).

## Ремонт и замена
Опишите проблему и приложите серийный номер. Инженер свяжется и назначит диагностику.

## Что важно
- Не вскрывайте корпус устройства самостоятельно.
- Персональные данные на диске сохраняются при ремонте, но резервную копию лучше сделать заранее.
- Замена расходников (картриджи, кабели) — через администратора этажа.
""",
    ),
    (
        "Работа с графиком отпусков для руководителя",
        "Кадры",
        "Отпуска",
        "руководитель, согласование, график, перекрытие",
        """## Роль руководителя
Вы — первый согласующий в маршруте. Задания приходят в раздел **На согласовании** и в уведомления.

## На что смотреть
1. Пересечение отпусков внутри отдела — портал показывает коллег с отпуском в те же даты.
2. Загруженность: если одновременно отсутствует более 40% состава, стоит предложить другие даты.
3. Соблюдение сроков подачи и остатка дней сотрудника.

## Если даты не подходят
Отклоните заявление с комментарием — сотрудник сразу увидит причину и сможет выбрать другие даты.

> Отклонение без причины невозможно: портал требует заполнить комментарий.
""",
    ),
    (
        "Антикоррупционная политика: кратко для сотрудника",
        "Компания",
        "Регламенты",
        "политика, комплаенс, подарки",
        """## Основные принципы
- Запрещены подарки и вознаграждения, влияющие на деловые решения.
- Любые конфликты интересов декларируются руководителю.
- Переговоры с контрагентами ведутся в присутствии второго сотрудника, если обсуждаются цены.

## Как сообщить о нарушении
Направить обращение в службу комплаенса через портал (категория «Другое», пометка «конфиденциально»)
или сообщить руководителю.

> Полный текст политики — в разделе «Регламенты» базы знаний.
""",
    ),
]

QUICK_LINKS = [
    ("Почта", "https://mail.ecros.local", "mail", "Корпоративная почта"),
    ("1С:Документооборот", "https://edo.ecros.local", "file", "Электронный документооборот"),
    ("Общие папки", "https://fs01.ecros.local", "database", "Файловый сервер"),
    ("Техподдержка", "https://sd.ecros.local", "help", "Портал Service Desk"),
    ("Телефонный справочник", "/directory/", "phone", "Справочник сотрудников", False),
    ("Календарь отпусков", "/vacations/calendar/", "calendar", "Кто в отпуске", False),
]

TICKETS = [
    ("Не работает VPN из дома", "it", "high", "С пятницы не подключается VPN — ошибка 809. Перезагрузка ноутбука не помогает."),
    ("Нужен доступ к папке отдела продаж", "access", "normal", "Требуется доступ на изменение к \\\\fs01\\share\\sales."),
    ("Не печатает принтер на 4 этаже", "it", "normal", "Принтер HP на 4 этаже показывает ошибку тонера, замена не помогла."),
    ("Копия трудовой книжки для банка", "hr", "normal", "Нужна заверенная копия трудовой книжки и справка о доходах за год."),
    ("Перегорела лампа в кабинете 312", "office", "low", "Мерцает лампа у окна, работать в кабинете вечером некомфортно."),
    ("Продлить пропуск для гостя", "office", "normal", "Нужен разовый пропуск на встречу с подрядчиком."),
    ("Не открывается 1С после обновления", "it", "critical", "После обновления 1С выдаёт ошибку лицензии при запуске."),
    ("Изменить график отпусков в 1С", "hr", "low", "Прошу внести изменения в график отпусков на следующий год."),
    ("Подключить второй монитор", "it", "normal", "Есть монитор и кабель, нужна помощь с подключением."),
    ("Ошибка при входе в портал", "access", "high", "При входе в портал пишет «учётная запись отключена»."),
    ("Замена картриджа в кабинете 208", "office", "normal", "Закончился тонер, требуется замена."),
    ("Доступ к ЭДО для нового сотрудника", "access", "normal", "Сотрудник вышел сегодня, нужен доступ в документооборот."),
]

DEMO_COMMENTS_APPROVE = [
    "Согласовано, график позволяет.",
    "Ок, с учётом проектных сроков возражений нет.",
    "Согласовано, дни в пределах остатка.",
]
DEMO_COMMENTS_REJECT = [
    "В эти даты в отделе не хватает сотрудников, прошу сдвинуть на неделю позже.",
    "Период совпадает с закрытием квартала — выберите другие даты.",
    "Превышен остаток дней по графику, обратитесь в отдел кадров.",
]


class Command(BaseCommand):
    help = "Наполняет портал демонстрационными данными (400 сотрудников, график отпусков, контент)"

    def add_arguments(self, parser):
        parser.add_argument("--employees", type=int, default=400, help="Число сотрудников в мок-каталоге")
        parser.add_argument("--no-sync", action="store_true", help="Не запускать синхронизацию с каталогом")
        parser.add_argument("--no-vacations", action="store_true", help="Не создавать заявления на отпуск")
        parser.add_argument("--admin-username", default="portal.admin", help="Логин локального администратора")
        parser.add_argument(
            "--admin-password", default="PortalAdmin#2026", help="Пароль локального администратора"
        )
        parser.add_argument("--seed", type=int, default=42, help="Зерно генератора случайных данных")

    def handle(self, *args, **options):
        self.random = random.Random(options["seed"])
        self.stdout.write(self.style.MIGRATE_HEADING("1/7 Роли портала"))
        self._seed_roles()
        self.stdout.write(self.style.MIGRATE_HEADING("2/7 Виды отпусков и производственный календарь"))
        self._seed_vacation_types()
        self._seed_holidays()
        self.stdout.write(self.style.MIGRATE_HEADING("3/7 Синхронизация с каталогом"))
        self._run_sync(options)
        self.stdout.write(self.style.MIGRATE_HEADING("4/7 Оргструктура и ответственные по ролям"))
        self._link_departments()
        self._assign_role_heads()
        self.stdout.write(self.style.MIGRATE_HEADING("5/7 Периоды графиков, маршруты, права на дни"))
        self._seed_periods()
        self._seed_chains()
        self._seed_balances()
        self.stdout.write(self.style.MIGRATE_HEADING("6/7 Контент портала"))
        self._seed_admin(options)
        self._seed_content()
        self._seed_tickets()
        if not options["no_vacations"]:
            self.stdout.write(self.style.MIGRATE_HEADING("7/7 Демонстрационные заявления на отпуск"))
            self._seed_vacation_requests()
        else:
            self.stdout.write(self.style.MIGRATE_HEADING("7/7 Демонстрационные заявления пропущены"))

        self._summary(options)

    # -- вспомогательное --------------------------------------------------
    @staticmethod
    def _employees_of_department(keyword: str):
        """Поиск сотрудников подразделения без учёта регистра.

        SQLite не умеет регистронезависимый LIKE для кириллицы, поэтому
        фильтруем на стороне Python — так команда одинаково работает на
        SQLite и PostgreSQL.
        """
        keyword = keyword.lower()
        departments = [d for d in Department.objects.all() if keyword in d.name.lower()]
        ids = set()
        for department in departments:
            ids.update(department.descendant_ids(include_self=True))
        if not ids:
            return Employee.objects.none()
        return Employee.objects.filter(department_id__in=ids, is_active=True)

    @classmethod
    def _department_head_like(cls, keyword: str):
        staff = cls._employees_of_department(keyword)
        return staff.filter(job_title__icontains="руководитель").order_by("last_name").first() or staff.first()

    # -- справочники ------------------------------------------------------
    def _seed_roles(self):
        for code, name, groups, order, description in ROLES:
            role, created = Role.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "ad_groups": groups,
                    "sort_order": order,
                    "description": description,
                    "is_system": True,
                    "is_active": True,
                },
            )
            self.stdout.write(f"  {'+ ' if created else '= '}{role.name} ← {', '.join(groups)}")

    def _seed_vacation_types(self):
        for code, name, days, consumes, approval, color, order in VACATION_TYPES:
            VacationType.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "default_days": days,
                    "consumes_balance": consumes,
                    "requires_approval": approval,
                    "color": color,
                    "sort_order": order,
                    "is_active": True,
                },
            )
        count = VacationType.objects.count()
        self.stdout.write(f"  видов отпуска: {count}")

    def _seed_holidays(self):
        today = timezone.localdate()
        created = 0
        for year in (today.year, today.year + 1, today.year + 2):
            for (month, day), name in HOLIDAYS_RU:
                try:
                    target = date(year, month, day)
                except ValueError:
                    continue
                _, was_created = Holiday.objects.get_or_create(
                    date=target, defaults={"name": name, "is_working": False}
                )
                created += int(was_created)
        self.stdout.write(f"  добавлено праздничных дней: {created}")

    # -- синхронизация ----------------------------------------------------
    def _run_sync(self, options):
        if options["no_sync"]:
            self.stdout.write("  синхронизация пропущена (--no-sync)")
            return
        provider = provider_name()
        if provider != "mock":
            self.stdout.write(
                self.style.WARNING(
                    f"  провайдер каталога: {provider} — синхронизация с боевой AD, это может занять время"
                )
            )
        try:
            directory = get_directory()
        except AdError as exc:
            self.stdout.write(self.style.ERROR(f"  каталог недоступен: {exc}"))
            return
        service = AdSyncService(directory=directory)
        run = service.run()
        counters = run.counters or {}
        style = self.style.SUCCESS if run.status == "success" else self.style.WARNING
        self.stdout.write(
            style(
                f"  создано: {counters.get('created', 0)}, обновлено: {counters.get('updated', 0)}, "
                f"подразделений: {counters.get('departments_created', 0)}, "
                f"ролей выдано: {counters.get('roles_assigned', 0)}"
            )
        )
        if run.status == "failed":
            self.stdout.write(self.style.ERROR(f"  синхронизация не выполнена: {run.message}"))
            return
        for warning in counters.get("warnings", [])[:5]:
            self.stdout.write(self.style.WARNING(f"  ! {warning}"))

    def _link_departments(self):
        """Выстраивает «Генеральная дирекция» ↔ дирекции по смыслу названий."""
        root = Department.objects.filter(name__icontains="Генеральная дирекция").first()
        if root is None:
            return
        directorates = Department.objects.filter(
            name__in=[
                "Коммерческая дирекция",
                "Финансовая дирекция",
                "Производственная дирекция",
                "Техническая дирекция",
                "Дирекция по персоналу",
                "Административная дирекция",
            ]
        )
        updated = 0
        for department in directorates:
            if department.parent_id != root.pk:
                department.parent = root
                department.save(update_fields=["parent", "updated_at"])
                updated += 1
        general = Employee.objects.filter(job_title__icontains="Генеральный директор").first()
        if general is not None and root.head_id != general.pk:
            root.head = general
            root.save(update_fields=["head", "updated_at"])
        self.stdout.write(f"  дирекций подчинено «Генеральной дирекции»: {updated}")

    def _assign_role_heads(self):
        general = self._employees_of_department("Генеральная дирекция").first()
        candidates = {
            "hr": self._department_head_like("кадр"),
            "director": general,
            "admin": self._department_head_like("ИТ-отдел") or self._department_head_like("разработки"),
            "manager": general,
        }
        for code, employee in candidates.items():
            if employee is None:
                continue
            role = Role.objects.filter(code=code).first()
            if role is not None and role.head_id != employee.pk:
                role.head = employee
                role.save(update_fields=["head"])
                self.stdout.write(f"  ответственный по роли «{role.name}»: {employee.full_name}")

    # -- периоды и маршруты ----------------------------------------------
    def _seed_periods(self):
        today = timezone.localdate()
        current, created = SchedulePeriod.objects.get_or_create(
            year=today.year,
            defaults={
                "status": SchedulePeriod.Status.REVIEW,
                "submission_deadline": today + timedelta(days=30),
                "note": "Текущий год: заявления принимаются до конца месяца",
            },
        )
        if created:
            self.stdout.write(f"  + период {current.year}")
        upcoming, created = SchedulePeriod.objects.get_or_create(
            year=today.year + 1,
            defaults={
                "status": SchedulePeriod.Status.COLLECTING,
                "submission_deadline": date(today.year, 12, 1),
                "note": "Формирование графика на следующий год",
            },
        )
        if created:
            self.stdout.write(f"  + период {upcoming.year}")

    def _seed_chains(self):
        hr_role = Role.objects.filter(code="hr").first()
        director_role = Role.objects.filter(code="director").first()
        hr_department = Department.objects.filter(name="Отдел кадров").first() or Department.objects.first()

        standard, _ = ApprovalChainTemplate.objects.update_or_create(
            code="standard",
            defaults={
                "name": "Стандартный маршрут: руководитель → кадры",
                "description": "Базовый маршрут для всех подразделений.",
                "is_default": True,
                "is_active": True,
                "priority": 100,
            },
        )
        self._sync_steps(
            standard,
            [
                (1, "Согласование руководителя", ApprovalChainStep.ApproverKind.MANAGER, None, None),
                (2, "Проверка отдела кадров", ApprovalChainStep.ApproverKind.HR, hr_role, None),
            ],
        )

        if hr_department is not None:
            hr_chain, _ = ApprovalChainTemplate.objects.update_or_create(
                code="hr-department",
                defaults={
                    "name": "Отдел кадров: руководитель → директор направления",
                    "description": "Кадровые заявления согласует директор по персоналу.",
                    "department": hr_department,
                    "is_active": True,
                    "priority": 10,
                },
            )
            self._sync_steps(
                hr_chain,
                [
                    (1, "Согласование руководителя", ApprovalChainStep.ApproverKind.MANAGER, None, None),
                    (2, "Директор направления", ApprovalChainStep.ApproverKind.DIRECTOR, director_role, None),
                ],
            )

        if director_role is not None:
            directors_chain, _ = ApprovalChainTemplate.objects.update_or_create(
                code="directors",
                defaults={
                    "name": "Директора направлений",
                    "description": "Руководитель → кадры: верхний уровень согласования.",
                    "is_active": True,
                    "priority": 20,
                },
            )
            directors_chain.applies_to_roles.set([director_role])
            self._sync_steps(
                directors_chain,
                [
                    (1, "Согласование руководителя", ApprovalChainStep.ApproverKind.MANAGER, None, None),
                    (2, "Проверка отдела кадров", ApprovalChainStep.ApproverKind.HR, hr_role, None),
                ],
            )
        self.stdout.write(f"  маршрутов согласования: {ApprovalChainTemplate.objects.count()}")

    def _sync_steps(self, chain, steps):
        chain.steps.all().delete()
        for order, title, kind, role, user in steps:
            ApprovalChainStep.objects.create(
                template=chain,
                order=order,
                title=title,
                approver_kind=kind,
                role=role,
                user=user,
                skip_if_missing=True,
            )

    def _seed_balances(self):
        total = 0
        for period in SchedulePeriod.objects.all():
            total += vacation_services.ensure_balances_for_period(period)
        self.stdout.write(f"  оформлено прав на отпуск: {total}")

    # -- пользователи и контент ------------------------------------------
    def _seed_admin(self, options):
        username = options["admin_username"]
        employee = Employee.objects.filter(username=username).first()
        department = self._employees_of_department("ИТ").first().department if self._employees_of_department("ИТ").exists() else None
        if employee is None:
            employee = Employee.objects.create(
                username=username,
                email=f"{username}@example.local",
                last_name="Администратор",
                first_name="Портал",
                display_name="Администратор портала",
                job_title="Администратор портала",
                department=department,
                source=Employee.Source.LOCAL,
                status=Employee.Status.ACTIVE,
            )
            self.stdout.write(f"  + локальный администратор {username}")
        employee.set_password(options["admin_password"])
        employee.is_staff = True
        employee.is_superuser = True
        employee.is_active = True
        employee.save()
        admin_role = Role.objects.filter(code="admin").first()
        if admin_role is not None:
            admin_role.members.add(employee)
        hr_role = Role.objects.filter(code="hr").first()
        if hr_role is not None:
            hr_role.members.add(employee)
        self.stdout.write(
            self.style.WARNING(
                f"  вход: {username} / {options['admin_password']} — смените пароль после первого входа"
            )
        )

    def _seed_content(self):
        categories = {}
        for name, code, color in (
            ("Компания", "company", "#00875c"),
            ("Кадры", "hr", "#00a651"),
            ("ИТ", "it", "#0f7b8f"),
        ):
            categories[name], _ = NewsCategory.objects.get_or_create(
                code=code, defaults={"name": name, "color": color, "sort_order": 10}
            )

        authors = list(Employee.objects.filter(is_active=True).order_by("last_name")[:12])
        hr_author = self._employees_of_department("кадр").first()
        it_author = self._employees_of_department("ИТ").first()

        created_news = 0
        for offset, (title, category_name, summary, body) in enumerate(NEWS):
            if NewsPost.objects.filter(title=title).exists():
                continue
            NewsPost.objects.create(
                title=title,
                category=categories.get(category_name),
                summary=summary,
                body=body,
                author=hr_author if category_name == "Кадры" else (it_author or (authors[0] if authors else None)),
                is_pinned=offset == 0,
                is_published=True,
                published_at=timezone.now() - timedelta(days=offset * 3),
            )
            created_news += 1
        self.stdout.write(f"  новостей добавлено: {created_news}")

        kb_categories = {}
        for name, code, icon in (
            ("Онбординг", "onboarding", "users"),
            ("ИТ и оборудование", "it", "briefcase"),
            ("Отпуска", "vacations", "calendar"),
            ("Регламенты", "regulations", "shield"),
        ):
            kb_categories[name], _ = KbCategory.objects.get_or_create(
                code=code, defaults={"name": name, "icon": icon, "sort_order": 10}
            )

        created_kb = 0
        for title, category_name, tag, tags, body in KB_ARTICLES:
            if KnowledgeArticle.objects.filter(title=title).exists():
                continue
            KnowledgeArticle.objects.create(
                title=title,
                category=kb_categories.get(category_name),
                summary=tag,
                tags=tags,
                body=body,
                author=hr_author if category_name in {"Онбординг", "Отпуска"} else it_author,
                is_published=True,
            )
            created_kb += 1
        self.stdout.write(f"  статей базы знаний добавлено: {created_kb}")

        for order, item in enumerate(QUICK_LINKS, start=1):
            title, url, icon, description = item[0], item[1], item[2], item[3]
            new_tab = item[4] if len(item) > 4 else True
            QuickLink.objects.get_or_create(
                title=title,
                defaults={
                    "url": url,
                    "icon": icon,
                    "description": description,
                    "sort_order": order * 10,
                    "open_in_new_tab": new_tab,
                },
            )

    def _seed_tickets(self):
        if ServiceTicket.objects.exists():
            self.stdout.write("  заявки уже есть — пропуск")
            return
        employees = list(Employee.objects.filter(is_active=True).order_by("?")[:40])
        if not employees:
            return
        support = self._employees_of_department("ИТ").first() or employees[0]
        hr_support = self._employees_of_department("кадр").first() or support
        statuses = [
            ServiceTicket.Status.NEW,
            ServiceTicket.Status.IN_PROGRESS,
            ServiceTicket.Status.WAITING,
            ServiceTicket.Status.RESOLVED,
            ServiceTicket.Status.CLOSED,
        ]
        created = 0
        for index, (subject, category, priority, description) in enumerate(TICKETS):
            requester = employees[index % len(employees)]
            ticket = ServiceTicket.objects.create(
                subject=subject,
                description=description,
                category=category,
                priority=priority,
                status=self.random.choice(statuses),
                requester=requester,
                assignee=hr_support if category == "hr" else support,
                created_at=timezone.now() - timedelta(days=self.random.randint(1, 40)),
            )
            if ticket.status != ServiceTicket.Status.NEW:
                TicketComment.objects.create(
                    ticket=ticket,
                    author=ticket.assignee,
                    body="Заявка принята в работу, уточняем детали у заявителя.",
                )
                TicketComment.objects.create(
                    ticket=ticket,
                    author=requester,
                    body="Спасибо, жду обратной связи.",
                )
            created += 1
        self.stdout.write(f"  заявок в поддержку: {created}")

    # -- заявления на отпуск ---------------------------------------------
    def _seed_vacation_requests(self):
        if VacationRequest.objects.exists():
            self.stdout.write("  заявления уже есть — пропуск")
            return
        today = timezone.localdate()
        periods = list(SchedulePeriod.objects.filter(year__gte=today.year).order_by("year"))
        if not periods:
            return
        annual = VacationType.objects.filter(code="annual").first()
        if annual is None:
            return

        employees = list(Employee.objects.filter(is_active=True).select_related("department", "manager"))
        if not employees:
            return
        # каждый четвёртый — с заявлением
        sample = [e for e in employees if self.random.random() < 0.32][:160]

        created = approved = pending = rejected = 0
        used_ranges: dict[tuple[int, int], list[tuple[date, date]]] = {}

        for employee in sample:
            period = self.random.choice(periods)
            year = period.year
            if period.year == today.year:
                earliest = today + timedelta(days=10)
            else:
                earliest = date(year, 1, 15)
            start = earliest + timedelta(days=self.random.randint(0, 240 if year > today.year else 150))
            duration = self.random.choice([7, 7, 10, 14, 14, 21])
            end = start + timedelta(days=duration - 1)
            if end.year != year:
                continue

            ranges = used_ranges.setdefault((employee.pk, year), [])
            if any(not (end < s or start > e) for s, e in ranges):
                continue
            ranges.append((start, end))

            request_obj = VacationRequest.objects.create(
                employee=employee,
                period=period,
                vacation_type=annual,
                start_date=start,
                end_date=end,
                days_count=vacation_services.count_vacation_days(start, end),
                employee_comment=self.random.choice(
                    ["", "", "Планирую поездку заранее.", "Прошу учесть семейные обстоятельства."]
                ),
            )
            try:
                vacation_services.submit_request(request_obj, actor=employee)
            except vacation_services.VacationError as exc:
                request_obj.delete()
                self.stdout.write(f"  ! {employee.short_name}: {exc}")
                continue
            created += 1

            scenario = self.random.choices(
                ["approved", "pending", "partial", "rejected"], weights=[58, 22, 14, 6]
            )[0]
            try:
                if scenario == "approved":
                    self._drive_approvals(request_obj, approve_all=True)
                elif scenario == "partial":
                    self._drive_approvals(request_obj, approve_all=False)
                elif scenario == "rejected":
                    self._drive_approvals(request_obj, reject_at=self.random.choice([0, 1]))
                # pending — оставляем на первом шаге
            except vacation_services.VacationError as exc:
                self.stdout.write(f"  ! согласование {employee.short_name}: {exc}")

            request_obj.refresh_from_db(fields=["status"])
            if request_obj.status == VacationRequest.Status.APPROVED:
                approved += 1
            elif request_obj.status == VacationRequest.Status.REJECTED:
                rejected += 1
            elif request_obj.status == VacationRequest.Status.PENDING:
                pending += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"  заявлений создано: {created} · согласовано: {approved} · "
                f"на согласовании: {pending} · отклонено: {rejected}"
            )
        )

    def _drive_approvals(self, request_obj: VacationRequest, *, approve_all: bool = True, reject_at: int | None = None):
        guard = 0
        step_index = 0
        while request_obj.status == VacationRequest.Status.PENDING and guard < 12:
            guard += 1
            step = request_obj.current_step
            if step is None or step.approver is None:
                break
            if reject_at is not None and step_index == reject_at:
                vacation_services.reject_step(
                    step, step.approver, self.random.choice(DEMO_COMMENTS_REJECT)
                )
                return
            if not approve_all and step_index == 1:
                return
            vacation_services.approve_step(step, step.approver, self.random.choice(DEMO_COMMENTS_APPROVE))
            step_index += 1
            request_obj.refresh_from_db(fields=["status"])

    # -- итоги ------------------------------------------------------------
    def _summary(self, options):
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Портал наполнен."))
        self.stdout.write(f"  сотрудников: {Employee.objects.count()} (активных {Employee.objects.filter(is_active=True).count()})")
        self.stdout.write(f"  подразделений: {Department.objects.count()}")
        self.stdout.write(f"  ролей: {Role.objects.count()}")
        self.stdout.write(f"  заявлений на отпуск: {VacationRequest.objects.count()}")
        self.stdout.write(f"  новостей: {NewsPost.objects.count()} · статей: {KnowledgeArticle.objects.count()}")
        self.stdout.write(f"  заявок в поддержку: {ServiceTicket.objects.count()}")
        self.stdout.write("")
        self.stdout.write("Запуск сервера:  python manage.py runserver 0.0.0.0:8000")
        self.stdout.write(f"Вход администратора:  {options['admin_username']} / {options['admin_password']}")
        if provider_name() == "mock":
            self.stdout.write(
                "Вход сотрудника: любой логин из справочника (например первый из списка) и пароль от 3 символов."
            )
