"""Демонстрационный источник данных «AD».

Используется, когда портал поднимают без доступа к контроллеру домена:
локальная разработка, пилот, автотесты. Генерирует детерминированную
оргструктуру на 400 сотрудников с руководителями, группами и телефонами —
этого достаточно, чтобы прогнать синхронизацию, график отпусков и маршруты
согласования ровно так же, как на боевой AD.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone as dt_timezone

from ..utils import make_username
from .base import AdDirectory, AdGroupRecord, AdHealth, AdUser

DOMAIN = "ecros.local"
BASE_DC = "DC=ecros,DC=local"
USERS_OU = "OU=Сотрудники"
GROUPS_OU = f"OU=Groups,{BASE_DC}"

MALE_LAST = [
    "Иванов", "Смирнов", "Кузнецов", "Попов", "Васильев", "Петров", "Соколов",
    "Михайлов", "Новиков", "Фёдоров", "Морозов", "Волков", "Алексеев", "Лебедев",
    "Семёнов", "Егоров", "Павлов", "Козлов", "Степанов", "Николаев", "Орлов",
    "Андреев", "Макаров", "Никитин", "Захаров", "Зайцев", "Соловьёв", "Борисов",
    "Яковлев", "Григорьев", "Романов", "Воробьёв", "Сергеев", "Кузьмин", "Фролов",
    "Александров", "Дмитриев", "Королёв", "Гусев", "Киселёв", "Ильин", "Максимов",
    "Поляков", "Сорокин", "Виноградов", "Ковалёв", "Белов", "Медведев", "Антонов",
    "Тарасов", "Жуков", "Баранов", "Филиппов", "Комаров", "Давыдов", "Беляев",
    "Герасимов", "Богданов", "Осипов", "Сидоров",
]
FEMALE_LAST = [
    "Иванова", "Смирнова", "Кузнецова", "Попова", "Васильева", "Петрова", "Соколова",
    "Михайлова", "Новикова", "Фёдорова", "Морозова", "Волкова", "Алексеева",
    "Лебедева", "Семёнова", "Егорова", "Павлова", "Козлова", "Степанова",
    "Николаева", "Орлова", "Андреева", "Макарова", "Никитина", "Захарова",
    "Зайцева", "Соловьёва", "Борисова", "Яковлева", "Григорьева", "Романова",
    "Воробьёва", "Сергеева", "Кузьмина", "Фролова", "Александрова", "Дмитриева",
    "Королёва", "Гусева", "Киселёва", "Ильина", "Максимова", "Полякова",
    "Сорокина", "Виноградова", "Ковалёва", "Белова", "Медведева", "Антонова",
    "Тарасова", "Жукова", "Баранова", "Филиппова", "Комарова", "Давыдова",
    "Беляева", "Герасимова", "Богданова", "Осипова", "Сидорова",
]
MALE_FIRST = [
    "Александр", "Алексей", "Андрей", "Антон", "Артём", "Борис", "Вадим", "Василий",
    "Виктор", "Виталий", "Владимир", "Владислав", "Геннадий", "Георгий", "Григорий",
    "Денис", "Дмитрий", "Евгений", "Егор", "Иван", "Игорь", "Илья", "Кирилл",
    "Константин", "Леонид", "Максим", "Марат", "Михаил", "Никита", "Николай",
    "Олег", "Павел", "Пётр", "Роман", "Руслан", "Сергей", "Станислав", "Степан",
    "Тимур", "Фёдор", "Эдуард", "Юрий", "Ярослав",
]
FEMALE_FIRST = [
    "Александра", "Алина", "Алла", "Анастасия", "Анна", "Валентина", "Валерия",
    "Вера", "Виктория", "Галина", "Дарья", "Евгения", "Екатерина", "Елена",
    "Елизавета", "Жанна", "Зоя", "Инна", "Ирина", "Кристина", "Ксения",
    "Лариса", "Лидия", "Любовь", "Людмила", "Маргарита", "Марина", "Мария",
    "Надежда", "Наталья", "Нина", "Оксана", "Ольга", "Полина", "Раиса",
    "Светлана", "София", "Тамара", "Татьяна", "Ульяна", "Юлия", "Яна",
]
PATRONYMIC_MALE = [
    "Александрович", "Алексеевич", "Андреевич", "Борисович", "Васильевич",
    "Викторович", "Владимирович", "Геннадьевич", "Дмитриевич", "Евгеньевич",
    "Иванович", "Игоревич", "Константинович", "Леонидович", "Михайлович",
    "Николаевич", "Олегович", "Павлович", "Петрович", "Сергеевич", "Юрьевич",
]
PATRONYMIC_FEMALE = [
    "Александровна", "Алексеевна", "Андреевна", "Борисовна", "Васильевна",
    "Викторовна", "Владимировна", "Геннадьевна", "Дмитриевна", "Евгеньевна",
    "Ивановна", "Игоревна", "Константиновна", "Леонидовна", "Михайловна",
    "Николаевна", "Олеговна", "Павловна", "Петровна", "Сергеевна", "Юрьевна",
]

CITIES = ["Москва", "Санкт-Петербург", "Екатеринбург", "Новосибирск", "Казань"]
OFFICES = ["101", "102", "105", "207", "208", "212", "301", "305", "310", "412"]

# путь подразделения -> (вес численности, [(должность, шаг прироста)])
ORG = {
    "Коммерческая дирекция": {
        "head_title": "Коммерческий директор",
        "units": {
            "Отдел продаж": {
                "weight": 34,
                "titles": ["Менеджер по продажам", "Старший менеджер по продажам", "Аккаунт-менеджер"],
            },
            "Отдел маркетинга": {
                "weight": 16,
                "titles": ["Маркетолог", "Специалист по маркетингу", "Бренд-менеджер"],
            },
            "Клиентский сервис": {
                "weight": 22,
                "titles": ["Специалист клиентского сервиса", "Оператор", "Руководитель группы поддержки"],
            },
            "Отдел тендеров": {
                "weight": 9,
                "titles": ["Специалист по тендерам", "Тендерный менеджер"],
            },
        },
    },
    "Финансовая дирекция": {
        "head_title": "Финансовый директор",
        "units": {
            "Бухгалтерия": {
                "weight": 18,
                "titles": ["Бухгалтер", "Старший бухгалтер", "Бухгалтер по расчётам"],
            },
            "Планово-экономический отдел": {
                "weight": 10,
                "titles": ["Экономист", "Ведущий экономист", "Аналитик"],
            },
            "Казначейство": {
                "weight": 6,
                "titles": ["Специалист казначейства", "Финансовый контролёр"],
            },
        },
    },
    "Производственная дирекция": {
        "head_title": "Директор по производству",
        "units": {
            "Производственный участок": {
                "weight": 48,
                "titles": ["Инженер-технолог", "Оператор линии", "Мастер участка", "Слесарь-сборщик"],
            },
            "Склад": {
                "weight": 20,
                "titles": ["Кладовщик", "Комплектовщик", "Оператор склада"],
            },
            "Отдел логистики": {
                "weight": 14,
                "titles": ["Логист", "Специалист по ВЭД", "Диспетчер"],
            },
            "Отдел качества": {
                "weight": 11,
                "titles": ["Инженер по качеству", "Специалист ОТК", "Аудитор СМК"],
            },
        },
    },
    "Техническая дирекция": {
        "head_title": "Технический директор",
        "units": {
            "ИТ-отдел": {
                "weight": 18,
                "titles": ["Системный администратор", "Инженер технической поддержки", "Сетевой инженер"],
            },
            "Отдел разработки": {
                "weight": 26,
                "titles": ["Программист", "Ведущий разработчик", "QA-инженер", "Аналитик данных"],
            },
            "Сервисная служба": {
                "weight": 13,
                "titles": ["Сервисный инженер", "Инженер по гарантии"],
            },
            "Отдел эксплуатации": {
                "weight": 8,
                "titles": ["Инженер-электроник", "Техник по обслуживанию"],
            },
        },
    },
    "Дирекция по персоналу": {
        "head_title": "Директор по персоналу",
        "units": {
            "Отдел кадров": {
                "weight": 10,
                "titles": ["Специалист по кадрам", "Инспектор по кадрам", "HR-администратор"],
            },
            "Отдел обучения и развития": {
                "weight": 5,
                "titles": ["Специалист по обучению", "Тренер", "HR-бизнес-партнёр"],
            },
        },
    },
    "Административная дирекция": {
        "head_title": "Административный директор",
        "units": {
            "Юридический отдел": {
                "weight": 6,
                "titles": ["Юрист", "Ведущий юрист", "Юрисконсульт"],
            },
            "Административно-хозяйственный отдел": {
                "weight": 9,
                "titles": ["Специалист АХО", "Заведующий хозяйством", "Секретарь"],
            },
            "Служба безопасности": {
                "weight": 7,
                "titles": ["Специалист СБ", "Контролёр КПП", "Охранник"],
            },
        },
    },
}

PORTAL_GROUPS = [
    "ECROS-Portal-Users",
    "ECROS-Portal-Managers",
    "ECROS-Portal-HR",
    "ECROS-Portal-IT",
    "ECROS-Portal-Directors",
    "ECROS-Portal-Admins",
    "ECROS-Portal-Vacation-Approvers",
]
TECH_GROUPS = [
    "ECROS-VPN-Users", "ECROS-Email-Users", "ECROS-FileShare-Edit",
    "ECROS-Print-Color", "ECROS-1C-Users", "ECROS-EDO-Users",
]


class MockAdProvider(AdDirectory):
    """Синтетический каталог: оргструктура, руководители, группы."""

    name = "mock"

    def __init__(self, seed: int = 20240501, employee_count: int = 400):
        self._random = random.Random(seed)
        self._employee_count = employee_count
        self._users: list[AdUser] | None = None
        self._groups: list[AdGroupRecord] = []

    # -- генерация --------------------------------------------------------
    def _person_name(self, female: bool) -> tuple[str, str, str]:
        rnd = self._random
        if female:
            last = rnd.choice(FEMALE_LAST)
            first = rnd.choice(FEMALE_FIRST)
            patronymic = rnd.choice(PATRONYMIC_FEMALE)
        else:
            last = rnd.choice(MALE_LAST)
            first = rnd.choice(MALE_FIRST)
            patronymic = rnd.choice(PATRONYMIC_MALE)
        return last, first, patronymic

    def _build(self) -> list[AdUser]:
        rnd = self._random
        users: list[AdUser] = []
        used_usernames: set[str] = set()
        used_names: set[tuple[str, str, str]] = set()
        employee_no = 100100
        phone = 1200

        def unique_person(female: bool) -> tuple[str, str, str]:
            for _ in range(200):
                last, first, patronymic = self._person_name(female)
                if (last, first, patronymic) not in used_names:
                    used_names.add((last, first, patronymic))
                    return last, first, patronymic
            last, first, patronymic = self._person_name(female)
            return last, first, f"{patronymic}{rnd.randint(2, 99)}"

        def unique_username(first: str, last: str) -> str:
            base = make_username(first, last)
            candidate = base
            index = 1
            while candidate in used_usernames:
                index += 1
                candidate = f"{base}{index}"
            used_usernames.add(candidate)
            return candidate

        def build_user(
            *,
            female: bool,
            title: str,
            ou_chain: list[str],
            manager_username: str = "",
            groups: list[str] | None = None,
            enabled: bool = True,
            department_name: str = "",
        ) -> AdUser:
            nonlocal employee_no, phone
            last, first, patronymic = unique_person(female)
            username = unique_username(first, last)
            employee_no += rnd.randint(1, 3)
            phone += 1
            ou_dn = ",".join([f"OU={part}" for part in reversed(ou_chain)])
            dn = f"CN={last} {first} {patronymic},{ou_dn},{USERS_OU},{BASE_DC}"
            hire = date(2008, 1, 1) + timedelta(days=rnd.randint(0, 6200))
            created = datetime(
                hire.year, hire.month, hire.day, tzinfo=dt_timezone.utc
            ) + timedelta(hours=rnd.randint(7, 18), minutes=rnd.randint(0, 59))
            changed = created + timedelta(days=rnd.randint(1, 400))
            group_names = list(
                dict.fromkeys(
                    (groups or [])
                    + _auto_groups(ou_chain[-1] if ou_chain else department_name, title)
                    + rnd.sample(TECH_GROUPS, rnd.randint(1, 3))
                )
            )
            group_dns = [f"CN={name},{GROUPS_OU}" for name in group_names]
            city = "Москва" if ou_chain and ou_chain[0] in {"Коммерческая дирекция", "Финансовая дирекция", "Дирекция по персоналу", "Административная дирекция"} else rnd.choice(CITIES)
            user = AdUser(
                username=username,
                dn=dn,
                guid=_fake_guid(username),
                email=f"{username}@{DOMAIN}",
                first_name=first,
                last_name=last,
                middle_name=patronymic,
                display_name=f"{last} {first} {patronymic}",
                job_title=title,
                department_name=department_name or (ou_chain[-1] if ou_chain else ""),
                manager_username=manager_username,
                manager_dn="",
                phone_internal=str(phone),
                phone_mobile=f"+7 (9{rnd.randint(10, 99)}) {rnd.randint(100, 999)}-{rnd.randint(10, 99)}-{rnd.randint(10, 99)}",
                office=rnd.choice(OFFICES),
                city=city,
                employee_no=str(employee_no),
                hire_date=hire,
                is_enabled=enabled,
                groups=group_names,
                group_dns=group_dns,
                when_changed=changed,
            )
            users.append(user)
            return user

        # 1. Генеральный директор
        general = build_user(
            female=False,
            title="Генеральный директор",
            ou_chain=["Генеральная дирекция"],
            groups=PORTAL_GROUPS[:1] + ["ECROS-Portal-Managers", "ECROS-Portal-Directors", "ECROS-Portal-Admins"],
            department_name="Генеральная дирекция",
        )

        # 2. Директора направлений и их подразделения
        head_by_unit: dict[str, str] = {}
        for directorate, spec in ORG.items():
            director = build_user(
                female=rnd.random() < 0.25,
                title=spec["head_title"],
                ou_chain=[directorate],
                manager_username=general.username,
                groups=["ECROS-Portal-Users", "ECROS-Portal-Managers", "ECROS-Portal-Directors", "ECROS-Portal-Vacation-Approvers"],
                department_name=directorate,
            )
            for unit, unit_spec in spec["units"].items():
                unit_head = build_user(
                    female=rnd.random() < 0.45,
                    title=f"Руководитель {unit.lower()}" if unit != "Бухгалтерия" else "Главный бухгалтер",
                    ou_chain=[directorate, unit],
                    manager_username=director.username,
                    groups=["ECROS-Portal-Users", "ECROS-Portal-Managers", "ECROS-Portal-Vacation-Approvers"],
                    department_name=unit,
                )
                head_by_unit[unit] = unit_head.username
                # старшие специалисты (руководители групп)
                group_leads = []
                for _ in range(max(1, unit_spec["weight"] // 12)):
                    lead = build_user(
                        female=rnd.random() < 0.5,
                        title=f"Ведущий {rnd.choice(unit_spec['titles']).lower()}",
                        ou_chain=[directorate, unit],
                        manager_username=unit_head.username,
                        groups=["ECROS-Portal-Users", "ECROS-Portal-Managers"],
                        department_name=unit,
                    )
                    group_leads.append(lead.username)
                # исполнители
                for _ in range(max(0, unit_spec["weight"] - len(group_leads))):
                    manager = rnd.choice(group_leads) if group_leads and rnd.random() < 0.6 else unit_head.username
                    build_user(
                        female=rnd.random() < 0.55,
                        title=rnd.choice(unit_spec["titles"]),
                        ou_chain=[directorate, unit],
                        manager_username=manager,
                        groups=["ECROS-Portal-Users"],
                        department_name=unit,
                    )

        # 3. Добор до нужной численности (внешние специалисты, помощники)
        extra_titles = [
            "Специалист", "Ассистент", "Помощник руководителя", "Техник",
            "Оператор", "Стажёр", "Ведущий специалист",
        ]
        while len(users) < self._employee_count:
            build_user(
                female=rnd.random() < 0.5,
                title=rnd.choice(extra_titles),
                ou_chain=["Коммерческая дирекция", "Отдел продаж"],
                manager_username=head_by_unit.get("Отдел продаж", ""),
                groups=["ECROS-Portal-Users"],
                department_name="Отдел продаж",
            )

        # 4. Особые случаи, которые должны корректно обрабатываться синхронизацией
        if users:
            # руководитель без логина руководителя (топ-1)
            # отключённые учётные записи уволенных
            for index in range(3):
                user = users[-1 - index]
                user.is_enabled = False
                user.job_title = f"{user.job_title} (уволен)"
            # сотрудник без подразделения (внешний консультант)
            outsider = users[5]
            outsider.department_name = ""
            outsider.dn = f"CN={outsider.display_name},{USERS_OU},{BASE_DC}"
            # сотрудник без e-mail
            users[7].email = ""
            # руководитель, который сам уже отключён
            users[9].is_enabled = False

        self._groups = self._build_groups(users)
        return users

    def _build_groups(self, users: list[AdUser]) -> list[AdGroupRecord]:
        counts: dict[str, int] = {}
        dns: dict[str, str] = {}
        for user in users:
            for name, dn in zip(user.groups, user.group_dns):
                counts[name] = counts.get(name, 0) + 1
                dns[name] = dn
        records = [
            AdGroupRecord(
                dn=dns.get(name, f"CN={name},{GROUPS_OU}"),
                name=name,
                description=(
                    "Портальная группа: управление ролями в корпоративном портале"
                    if name.startswith("ECROS-Portal")
                    else "Техническая группа доступа"
                ),
                member_count=count,
            )
            for name, count in sorted(counts.items())
        ]
        return records

    def _ensure(self) -> list[AdUser]:
        if self._users is None:
            self._users = self._build()
        return self._users

    # -- интерфейс AdDirectory -------------------------------------------
    def fetch_users(self, since: datetime | None = None) -> list[AdUser]:
        users = self._ensure()
        if since is None:
            return list(users)
        if since.tzinfo is None:
            since = since.replace(tzinfo=dt_timezone.utc)
        result = []
        for user in users:
            changed = user.when_changed
            if changed is None:
                result.append(user)
            elif changed.tzinfo is None and changed >= since.replace(tzinfo=None):
                result.append(user)
            elif changed.tzinfo is not None and changed >= since:
                result.append(user)
        return result

    def fetch_groups(self) -> list[AdGroupRecord]:
        self._ensure()
        return list(self._groups)

    def fetch_user(self, username: str) -> AdUser | None:
        needle = (username or "").strip().lower()
        for user in self._ensure():
            if user.username.lower() == needle:
                return user
        return None

    def authenticate(self, username: str, password: str) -> bool:
        if not password or len(password) < 3:
            return False
        user = self.fetch_user(username)
        if user is None or not user.is_enabled:
            return False
        return password.lower() not in {"wrong", "incorrect", "qwerty"}

    def healthcheck(self) -> AdHealth:
        users = self._ensure()
        return AdHealth(
            True,
            "Демонстрационный каталог: синтетические данные, контроллер домена не используется",
            {
                "provider": "mock",
                "users": len(users),
                "groups": len(self._groups),
                "domain": DOMAIN,
                "hint": "Чтобы перейти на боевую AD, задайте AD_PROVIDER=ldap и параметры AD_* в .env",
            },
        )


HR_UNITS = {"Отдел кадров", "Отдел обучения и развития"}
IT_UNITS = {"ИТ-отдел", "Отдел разработки"}
MANAGER_MARKERS = ("руководитель", "директор", "главный бухгалтер", "ведущий", "начальник")
ADMIN_MARKERS = ("руководитель ит-отдела", "руководитель отдела разработки", "руководитель отдела кадров")


def _auto_groups(unit_name: str, title: str) -> list[str]:
    """Выводит портальные группы из должности и подразделения.

    Боевая AD заполняется аналогично: доступ выдаётся членством в группах,
    поэтому мок должен повторять ту же логику.
    """
    groups: list[str] = []
    title_lower = (title or "").lower()
    is_manager = any(marker in title_lower for marker in MANAGER_MARKERS)
    if is_manager:
        groups.append("ECROS-Portal-Managers")
        groups.append("ECROS-Portal-Vacation-Approvers")
    if "директор" in title_lower and "генеральный" not in title_lower and "руководитель" not in title_lower:
        groups.append("ECROS-Portal-Directors")
    if unit_name in HR_UNITS:
        groups.append("ECROS-Portal-HR")
    if unit_name in IT_UNITS:
        groups.append("ECROS-Portal-IT")
    if any(marker in title_lower for marker in ADMIN_MARKERS):
        groups.append("ECROS-Portal-Admins")
    return groups


def _fake_guid(username: str) -> str:
    import hashlib
    import uuid

    digest = hashlib.md5(f"ecros::{username}".encode("utf-8")).digest()
    return str(uuid.UUID(bytes=digest))
