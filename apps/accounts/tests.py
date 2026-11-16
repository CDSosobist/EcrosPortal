"""Тесты интеграции с каталогом: маппинг, синхронизация, аутентификация."""
from __future__ import annotations

from django.test import TestCase, override_settings

from .ad.base import AdDirectory, AdGroupRecord, AdHealth, AdUser
from .ad.mock_provider import MockAdProvider
from .ad.sync import AdSyncService
from .auth import ActiveDirectoryBackend, LocalPasswordBackend
from .models import AdSyncRun, Department, Employee, Role
from .utils import dn_ou_chain, domain_from_base_dn, make_username, translit

DN_ROOT = "OU=Сотрудники,DC=ecros,DC=local"


def dn(cn: str, *ous: str) -> str:
    """Собирает distinguishedName: OU передаются от корня к вложенному.

    В самом DN порядок обратный — самым левым идёт самое вложенное подразделение.
    """
    parts = [f"CN={cn}"] + [f"OU={ou}" for ou in reversed(ous)] + [DN_ROOT]
    return ",".join(parts)


class FakeDirectory(AdDirectory):
    """Мини-каталог: предсказуемые данные без генерации 400 записей."""

    name = "fake"

    def __init__(self, users: list[AdUser]):
        self.users = users

    def fetch_users(self, since=None):
        return list(self.users)

    def fetch_groups(self):
        return [AdGroupRecord(dn=f"CN=ECROS-Portal-HR,DC=ecros,DC=local", name="ECROS-Portal-HR", member_count=2)]

    def fetch_user(self, username):
        return next((u for u in self.users if u.username.lower() == username.lower()), None)

    def authenticate(self, username, password):
        return password == "secret"

    def healthcheck(self):
        return AdHealth(True, "ok")


def build_directory(extra: list[AdUser] | None = None, drop: str | None = None) -> FakeDirectory:
    users = [
        AdUser(
            username="g.director",
            dn=dn("Директор Геннадий Петрович", "Генеральная дирекция"),
            guid="guid-director",
            email="g.director@ecros.local",
            first_name="Геннадий",
            last_name="Директор",
            display_name="Директор Геннадий Петрович",
            job_title="Генеральный директор",
            department_name="Генеральная дирекция",
            groups=["ECROS-Portal-Users", "ECROS-Portal-Directors", "ECROS-Portal-Managers"],
            group_dns=["CN=ECROS-Portal-Directors,DC=ecros,DC=local"],
        ),
        AdUser(
            username="a.head",
            dn=dn("Голова Анна Ивановна", "Генеральная дирекция", "Коммерческая дирекция"),
            guid="guid-head",
            email="a.head@ecros.local",
            first_name="Анна",
            last_name="Голова",
            display_name="Голова Анна Ивановна",
            job_title="Коммерческий директор",
            department_name="Коммерческая дирекция",
            manager_username="g.director",
            groups=["ECROS-Portal-Managers"],
        ),
        AdUser(
            username="i.sales",
            dn=dn("Продажник Иван Сергеевич", "Генеральная дирекция", "Коммерческая дирекция", "Отдел продаж"),
            guid="guid-sales",
            email="i.sales@ecros.local",
            first_name="Иван",
            last_name="Продажник",
            display_name="Продажник Иван Сергеевич",
            job_title="Менеджер по продажам",
            department_name="Отдел продаж",
            manager_username="a.head",
            phone_internal="1205",
            groups=["ECROS-Portal-Users"],
        ),
        AdUser(
            username="p.hr",
            dn=dn("Кадрова Полина Олеговна", "Генеральная дирекция", "Дирекция по персоналу", "Отдел кадров"),
            guid="guid-hr",
            email="p.hr@ecros.local",
            first_name="Полина",
            last_name="Кадрова",
            display_name="Кадрова Полина Олеговна",
            job_title="Руководитель отдела кадров",
            department_name="Отдел кадров",
            manager_username="g.director",
            groups=["ECROS-Portal-Users", "ECROS-Portal-HR"],
        ),
        AdUser(
            username="old.user",
            dn=dn("Уволенный Олег Петрович", "Генеральная дирекция", "Коммерческая дирекция", "Отдел продаж"),
            guid="guid-old",
            email="old@ecros.local",
            first_name="Олег",
            last_name="Уволенный",
            display_name="Уволенный Олег Петрович",
            department_name="Отдел продаж",
            is_enabled=False,
            groups=["ECROS-Portal-Users"],
        ),
        AdUser(
            username="no.mail",
            dn=dn("Безпочтовый Пётр Ильич", "Генеральная дирекция", "Коммерческая дирекция", "Отдел продаж"),
            guid="guid-nomail",
            first_name="Пётр",
            last_name="Безпочтовый",
            display_name="Безпочтовый Пётр Ильич",
            department_name="Отдел продаж",
            manager_username="a.head",
        ),
    ]
    if extra:
        users.extend(extra)
    if drop:
        users = [u for u in users if u.username != drop]
    return FakeDirectory(users)


class UtilsTest(TestCase):
    def test_translit_and_username(self):
        self.assertEqual(translit("Иванов"), "ivanov")
        self.assertEqual(translit("Щербаков"), "shcherbakov")
        self.assertEqual(make_username("Иван", "Иванов"), "i.ivanov")

    def test_dn_ou_chain_builds_hierarchy(self):
        chain = dn_ou_chain(dn("Тестов Тест", "Генеральная дирекция", "Коммерческая дирекция", "Отдел продаж"))
        self.assertEqual([name for _, name in chain], ["Генеральная дирекция", "Коммерческая дирекция", "Отдел продаж"])
        # контейнер «Сотрудники» не должен попадать в оргструктуру
        self.assertFalse(any(name.lower() == "сотрудники" for _, name in chain))

    def test_domain_from_base_dn(self):
        self.assertEqual(domain_from_base_dn(DN_ROOT), "ecros.local")


class SyncTest(TestCase):
    def setUp(self):
        self.role_hr = Role.objects.create(code="hr", name="Кадры", ad_groups=["ECROS-Portal-HR"])
        self.role_manager = Role.objects.create(
            code="manager", name="Руководители", ad_groups=["ECROS-Portal-Managers"]
        )
        self.role_director = Role.objects.create(
            code="director", name="Директора", ad_groups=["ECROS-Portal-Directors"]
        )
        self.directory = build_directory()

    def run_sync(self, directory=None, **kwargs):
        return AdSyncService(directory=directory or self.directory, **kwargs).run()

    def test_creates_employees_and_departments(self):
        run = self.run_sync()
        self.assertEqual(run.status, AdSyncRun.Status.SUCCESS)
        self.assertEqual(Employee.objects.count(), 6)

        sales = Department.objects.get(name="Отдел продаж")
        self.assertEqual(sales.parent.name, "Коммерческая дирекция")
        self.assertEqual(sales.parent.parent.name, "Генеральная дирекция")

        ivan = Employee.objects.get(username="i.sales")
        self.assertEqual(ivan.department, sales)
        self.assertEqual(ivan.job_title, "Менеджер по продажам")
        self.assertEqual(ivan.phone_internal, "1205")
        self.assertEqual(ivan.source, Employee.Source.AD)
        self.assertFalse(ivan.has_usable_password())

    def test_resolves_managers_and_department_heads(self):
        self.run_sync()
        ivan = Employee.objects.get(username="i.sales")
        head = Employee.objects.get(username="a.head")
        self.assertEqual(ivan.manager, head)
        sales = Department.objects.get(name="Отдел продаж")
        self.assertEqual(sales.head, head)
        commercial = Department.objects.get(name="Коммерческая дирекция")
        self.assertEqual(commercial.head, head)

    def test_assigns_roles_by_ad_groups(self):
        self.run_sync()
        hr = Employee.objects.get(username="p.hr")
        self.assertIn(hr, self.role_hr.members.all())
        self.assertTrue(hr.has_role("hr"))
        head = Employee.objects.get(username="a.head")
        self.assertIn(head, self.role_manager.members.all())

    def test_disabled_account_is_not_deactivated_in_portal_without_flag(self):
        self.run_sync()
        old = Employee.objects.get(username="old.user")
        self.assertFalse(old.is_active)

    def test_second_run_is_idempotent(self):
        self.run_sync()
        second = self.run_sync()
        self.assertEqual(second.counters["created"], 0)
        self.assertEqual(Employee.objects.count(), 6)
        self.assertEqual(Department.objects.count(), 5)

    def test_missing_user_is_deactivated(self):
        self.run_sync()
        shrunk = AdSyncService(directory=build_directory(drop="no.mail")).run()
        self.assertEqual(shrunk.status, AdSyncRun.Status.SUCCESS)
        gone = Employee.objects.get(username="no.mail")
        self.assertFalse(gone.is_active)
        self.assertTrue(gone.missing_in_ad)
        self.assertGreaterEqual(shrunk.counters["deactivated"], 1)

    def test_dry_run_does_not_write(self):
        run = AdSyncService(directory=self.directory, dry_run=True, mode=AdSyncRun.Mode.DRY_RUN).run()
        self.assertEqual(run.counters["users_fetched"], 6)
        self.assertEqual(Employee.objects.count(), 0)
        self.assertEqual(Department.objects.count(), 0)
        self.assertEqual(run.counters["created"], 6)

    def test_empty_directory_aborts_without_data_loss(self):
        self.run_sync()
        empty = AdSyncService(directory=FakeDirectory([])).run()
        self.assertEqual(empty.status, AdSyncRun.Status.FAILED)
        self.assertEqual(Employee.objects.filter(is_active=True).count(), 5)

    def test_single_user_provisioning(self):
        service = AdSyncService(directory=self.directory)
        ad_user = self.directory.fetch_user("i.sales")
        employee = service.sync_single_user(ad_user)
        self.assertIsNotNone(employee)
        self.assertEqual(employee.username, "i.sales")
        self.assertEqual(employee.department.name, "Отдел продаж")

    def test_audit_event_is_recorded(self):
        self.run_sync()
        self.assertTrue(AuditEventProxy.exists("ad.sync_completed"))


class AuditEventProxy:
    """Небольшая обёртка, чтобы не импортировать модель в каждый тест."""

    @staticmethod
    def exists(action: str) -> bool:
        from .models import AuditEvent

        return AuditEvent.objects.filter(action=action).exists()


@override_settings(AD={"PROVIDER": "mock", "ENABLED": True, "BASE_DN": "DC=ecros,DC=local", "TIMEOUT": 5})
class MockProviderTest(TestCase):
    def test_generates_corporate_scale_directory(self):
        provider = MockAdProvider()
        users = provider.fetch_users()
        self.assertEqual(len(users), 400)
        usernames = {u.username for u in users}
        self.assertEqual(len(usernames), 400)
        emails = [u.email for u in users if u.email]
        self.assertEqual(len(emails), len(set(emails)))
        self.assertGreater(len(provider.fetch_groups()), 5)

        with_manager = [u for u in users if u.manager_username]
        self.assertGreater(len(with_manager), 300)
        self.assertTrue(all(u.dn.startswith("CN=") for u in users))

    def test_mock_authentication(self):
        provider = MockAdProvider()
        first = provider.fetch_users()[0]
        self.assertTrue(provider.authenticate(first.username, "demo1234"))
        self.assertFalse(provider.authenticate(first.username, "wrong"))
        self.assertFalse(provider.authenticate("nobody-at-all", "demo1234"))


@override_settings(AD={"PROVIDER": "mock", "ENABLED": True, "TIMEOUT": 5, "BASE_DN": "DC=ecros,DC=local"})
class AuthenticationTest(TestCase):
    def setUp(self):
        self.backend = ActiveDirectoryBackend()
        self.provider = MockAdProvider()
        self.user = self.provider.fetch_users()[0]

    def test_ad_backend_logs_in_existing_employee(self):
        employee = Employee.objects.create(
            username=self.user.username,
            display_name=self.user.label,
            source=Employee.Source.AD,
        )
        authenticated = self.backend.authenticate(None, username=self.user.username, password="demo1234")
        self.assertIsNotNone(authenticated)
        self.assertEqual(authenticated.pk, employee.pk)
        authenticated.refresh_from_db()
        self.assertIsNotNone(authenticated.last_login)

    def test_ad_backend_creates_account_on_first_login(self):
        authenticated = self.backend.authenticate(None, username=self.user.username, password="demo1234")
        self.assertIsNotNone(authenticated)
        self.assertEqual(authenticated.username, self.user.username)
        self.assertEqual(authenticated.source, Employee.Source.AD)

    def test_ad_backend_rejects_wrong_password(self):
        self.assertIsNone(self.backend.authenticate(None, username=self.user.username, password="wrong"))

    def test_ad_backend_rejects_disabled_portal_account(self):
        Employee.objects.create(
            username=self.user.username,
            display_name=self.user.label,
            source=Employee.Source.AD,
            is_active=False,
        )
        self.assertIsNone(self.backend.authenticate(None, username=self.user.username, password="demo1234"))

    def test_local_backend_only_for_local_accounts(self):
        local = Employee.objects.create(username="portal.admin", source=Employee.Source.LOCAL)
        local.set_password("SuperSecret123")
        local.save()
        backend = LocalPasswordBackend()
        self.assertIsNotNone(backend.authenticate(None, username="portal.admin", password="SuperSecret123"))
        self.assertIsNone(backend.authenticate(None, username="portal.admin", password="nope"))

        domain = Employee.objects.create(username="d.user", source=Employee.Source.AD)
        domain.set_password("SuperSecret123")
        domain.save()
        self.assertIsNone(backend.authenticate(None, username="d.user", password="SuperSecret123"))
