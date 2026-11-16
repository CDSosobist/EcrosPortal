"""Синхронизация портала с Active Directory.

Правила:
  * ключ сопоставления — objectGUID, резервный — sAMAccountName (без регистра);
  * подразделения строятся по контейнерам OU в distinguishedName;
  * руководители проставляются по атрибуту ``manager``;
  * руководители подразделений определяются по фактической подчинённости;
  * роли портала выдаются по маскам групп AD (роли настраиваются в админке);
  * пропавшие из каталога учётные записи деактивируются (только при полной
    синхронизации), но не удаляются — история заявок и отпусков сохраняется.
"""
from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone as dt_timezone

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from ..models import AdGroup, AdSyncRun, AuditEvent, Department, Employee, Role
from ..utils import dn_ou_chain, slug_code
from .base import AdDirectory, AdError, AdUser
from .factory import get_directory

logger = logging.getLogger(__name__)

MAX_REASONABLE_DROP = 0.5  # защита от «пустой выдачи» при неверном фильтре


@dataclass
class SyncReport:
    """Итоги синхронизации — попадают и в протокол, и в интерфейс."""

    users_fetched: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    disabled_in_ad: int = 0
    deactivated: int = 0
    departments_created: int = 0
    departments_updated: int = 0
    department_heads: int = 0
    roles_assigned: int = 0
    managers_resolved: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "users_fetched": self.users_fetched,
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "disabled_in_ad": self.disabled_in_ad,
            "deactivated": self.deactivated,
            "departments_created": self.departments_created,
            "departments_updated": self.departments_updated,
            "department_heads": self.department_heads,
            "roles_assigned": self.roles_assigned,
            "managers_resolved": self.managers_resolved,
            "errors": self.errors[:50],
            "warnings": self.warnings[:50],
        }


class AdSyncService:
    """Один запуск синхронизации. Идемпотентен: повторный запуск не дублирует данные."""

    def __init__(
        self,
        directory: AdDirectory | None = None,
        *,
        actor: Employee | None = None,
        dry_run: bool = False,
        mode: str | None = None,
        request=None,
    ):
        self.directory = directory or get_directory()
        self.actor = actor
        self.dry_run = dry_run
        self.request = request
        self.mode = mode or (AdSyncRun.Mode.DRY_RUN if dry_run else AdSyncRun.Mode.FULL)
        self.report = SyncReport()
        self._username_index: dict[str, Employee] = {}
        self._skip_deactivation = False

    # -- публичный API ----------------------------------------------------
    def run(self, since: datetime | None = None) -> AdSyncRun:
        run = AdSyncRun.objects.create(
            provider=getattr(self.directory, "name", "unknown"),
            mode=self.mode,
            status=AdSyncRun.Status.RUNNING,
            triggered_by=self.actor if getattr(self.actor, "pk", None) else None,
        )
        started = timezone.now()
        try:
            users = self.directory.fetch_users(since=since if self.mode == AdSyncRun.Mode.INCREMENTAL else None)
        except AdError as exc:
            return self._fail(run, f"Не удалось прочитать каталог: {exc}")
        except Exception as exc:  # noqa: BLE001 - протокол должен зафиксировать любую ошибку
            logger.exception("Неожиданная ошибка чтения каталога")
            return self._fail(run, f"Неожиданная ошибка чтения каталога: {exc}")

        self.report.users_fetched = len(users)
        if not users and self.mode == AdSyncRun.Mode.INCREMENTAL and since is not None:
            # для инкрементального режима пустая выдача — нормальный результат
            run.finished_at = timezone.now()
            run.status = AdSyncRun.Status.SUCCESS
            run.counters = self.report.as_dict()
            run.message = "Инкрементальная синхронизация: изменений в каталоге нет"
            run.save()
            return run

        active_total = Employee.objects.filter(is_active=True).count()
        if not users:
            return self._fail(run, "Каталог вернул 0 пользователей — синхронизация отменена, данные не тронуты.", started)
        if active_total and len(users) < active_total * (1 - MAX_REASONABLE_DROP):
            self.report.warnings.append(
                f"Из каталога пришло {len(users)} учётных записей против {active_total} в портале — "
                "деактивация пропущена, проверьте фильтр поиска."
            )
            users = users  # данные всё равно обновляем, но не деактивируем
            self._skip_deactivation = True
        else:
            self._skip_deactivation = False

        if self.dry_run:
            with transaction.atomic():
                self._apply(users)
                transaction.set_rollback(True)
            return self._finish(run, started, dry_run=True)

        try:
            with transaction.atomic():
                self._apply(users)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Синхронизация с AD прервана")
            return self._fail(run, f"Синхронизация прервана, изменения отменены: {exc}", started)
        return self._finish(run, started)

    def sync_single_user(self, ad_user: AdUser, *, create: bool = True) -> Employee | None:
        """Точечная синхронизация одной записи (первый вход сотрудника)."""
        try:
            with transaction.atomic():
                self._sync_departments([ad_user])
                employee = self._upsert_employee(ad_user, create=create)
                if employee is not None:
                    self._resolve_manager(employee, ad_user)
                    self._assign_roles_for([employee])
            return employee
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось синхронизировать пользователя %s", ad_user.username)
            return None

    # -- этапы ------------------------------------------------------------
    def _apply(self, users: list[AdUser]) -> None:
        self._sync_departments(users)
        employees = [self._upsert_employee(user) for user in users]
        employees = [e for e in employees if e is not None]

        # Второй проход: руководители. Каталог может отдавать записи в любом порядке,
        # поэтому ссылка на руководителя проставляется только после создания всех карточек.
        by_username = {e.username.lower(): e for e in employees}
        for user in users:
            employee = by_username.get((user.username or "").lower())
            if employee is not None:
                self._resolve_manager(employee, user)

        self._sync_groups(self._fetch_groups_safe())
        self._assign_roles_for(employees)
        self._detect_department_heads()
        if not self._skip_deactivation and self.mode != AdSyncRun.Mode.INCREMENTAL:
            self._deactivate_missing({u.username.lower() for u in users})

    def _fetch_groups_safe(self):
        try:
            return self.directory.fetch_groups()
        except AdError as exc:
            self.report.warnings.append(f"Справочник групп недоступен: {exc}")
            return []

    # -- подразделения ----------------------------------------------------
    def _sync_departments(self, users: list[AdUser]) -> None:
        cache: dict[str, Department] = {
            dept.ad_dn: dept for dept in Department.objects.exclude(ad_dn="") if dept.ad_dn
        }
        cache_by_code: dict[str, Department] = {d.code: d for d in Department.objects.all()}

        for user in users:
            chain = dn_ou_chain(user.dn)
            if not chain:
                chain = [("", user.department_name)] if user.department_name else []
            parent: Department | None = None
            for position, (ou_dn, ou_name) in enumerate(chain):
                is_leaf = position == len(chain) - 1
                name = (user.department_name if is_leaf and user.department_name else ou_name).strip()
                if not name:
                    continue
                key = ou_dn or f"virtual:{name}"
                department = cache.get(key)
                if department is None:
                    code = self._unique_department_code(name, ou_dn)
                    department = Department.objects.create(
                        name=name[:200],
                        code=code,
                        ad_dn=(ou_dn or "")[:512],
                        parent=parent,
                        sort_order=100 + position,
                    )
                    cache[key] = department
                    cache_by_code[code] = department
                    self.report.departments_created += 1
                else:
                    updates: list[str] = []
                    if department.name != name[:200] and is_leaf:
                        department.name = name[:200]
                        updates.append("name")
                    if parent is not None and department.parent_id != parent.pk:
                        department.parent = parent
                        updates.append("parent")
                    if updates:
                        department.save(update_fields=updates + ["updated_at"])
                        self.report.departments_updated += 1
                parent = department

    def _unique_department_code(self, name: str, ou_dn: str) -> str:
        base = slug_code(name, fallback="department")[:60]
        candidate = base
        index = 1
        while Department.objects.filter(code=candidate).exists():
            index += 1
            candidate = f"{base}-{index}"
        return candidate

    def _department_for(self, user: AdUser) -> Department | None:
        chain = dn_ou_chain(user.dn)
        if chain:
            return Department.objects.filter(ad_dn=chain[-1][0]).first()
        if user.department_name:
            return Department.objects.filter(name__iexact=user.department_name).first()
        return None

    # -- сотрудники -------------------------------------------------------
    def _upsert_employee(self, user: AdUser, *, create: bool = True) -> Employee | None:
        try:
            employee = self._find_employee(user)
            department = self._department_for(user)
            ad_group_names = [g for g in user.groups if g]
            ad_group_dns = [d for d in user.group_dns if d]

            when_changed = user.when_changed
            if when_changed is not None and timezone.is_naive(when_changed):
                when_changed = timezone.make_aware(when_changed, dt_timezone.utc)

            values = {
                "last_name": user.last_name,
                "first_name": user.first_name,
                "middle_name": user.middle_name,
                "display_name": user.display_name or f"{user.last_name} {user.first_name}".strip(),
                "job_title": user.job_title,
                "employee_no": user.employee_no,
                "department": department,
                "phone_internal": user.phone_internal,
                "phone_mobile": user.phone_mobile,
                "office_room": user.office,
                "city": user.city,
                "hire_date": user.hire_date,
                "ad_dn": (user.dn or "")[:512],
                "ad_guid": (user.guid or "")[:64],
                "ad_groups": ad_group_names,
                "ad_group_dns": ad_group_dns,
                "ad_synced_at": timezone.now(),
                "ad_when_changed": when_changed,
                "missing_in_ad": False,
                "source": Employee.Source.AD,
                "is_active": bool(user.is_enabled),
            }
            if not user.is_enabled:
                self.report.disabled_in_ad += 1

            if employee is None:
                if not create:
                    return None
                username = user.username.strip().lower()
                if Employee.objects.filter(username=username).exists():
                    self.report.warnings.append(f"Логин {username} занят локальной учётной записью — пропущен")
                    return None
                email = self._unique_email(user.email, username)
                employee = Employee(username=username, email=email, **values)
                employee.set_unusable_password()
                employee.save()
                self.report.created += 1
                self._username_index[username] = employee
                return employee

            changed_fields: list[str] = []
            for field_name, value in values.items():
                if getattr(employee, field_name) != value:
                    setattr(employee, field_name, value)
                    changed_fields.append(field_name)
            if user.email and not employee.email:
                email = self._unique_email(user.email, employee.username)
                if email:
                    employee.email = email
                    changed_fields.append("email")
            if changed_fields:
                employee.save(update_fields=list(dict.fromkeys(changed_fields + ["updated_at"])))
                self.report.updated += 1
            else:
                self.report.unchanged += 1
            self._username_index[employee.username.lower()] = employee
            return employee
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка обработки пользователя %s", user.username)
            self.report.errors.append(f"{user.username}: {exc}")
            return None

    def _find_employee(self, user: AdUser) -> Employee | None:
        if user.guid:
            found = Employee.objects.filter(ad_guid=user.guid).first()
            if found:
                return found
        username = (user.username or "").strip().lower()
        if not username:
            return None
        return Employee.objects.filter(username=username).first()

    def _unique_email(self, email: str, username: str) -> str:
        email = (email or "").strip()
        if not email:
            return ""
        if not Employee.objects.filter(email__iexact=email).exists():
            return email
        local, _, domain = email.partition("@")
        candidate = f"{local}.{username}@{domain}" if domain else f"{local}.{username}"
        if not Employee.objects.filter(email__iexact=candidate).exists():
            self.report.warnings.append(f"Дубликат e-mail {email} → сохранён как {candidate}")
            return candidate
        self.report.warnings.append(f"Дубликат e-mail {email} у {username} — поле оставлено пустым")
        return ""

    def _resolve_manager(self, employee: Employee, user: AdUser) -> None:
        if not user.manager_username:
            return
        manager = self._username_index.get(user.manager_username.lower())
        if manager is None:
            manager = Employee.objects.filter(
                username=user.manager_username.strip().lower()
            ).first()
            if manager is not None:
                self._username_index[manager.username.lower()] = manager
        if manager is None:
            manager = self._upsert_employee(
                AdUser(username=user.manager_username, display_name=user.manager_username), create=False
            )
        if manager is None or manager.pk == employee.pk:
            if manager is None:
                self.report.warnings.append(
                    f"Руководитель {user.manager_username} для {employee.username} не найден в каталоге"
                )
            return
        if employee.manager_id != manager.pk:
            employee.manager = manager
            employee.save(update_fields=["manager", "updated_at"])
            self.report.managers_resolved += 1

    def _detect_department_heads(self) -> None:
        """Определяет руководителей подразделений по фактической подчинённости.

        Правила по приоритету:
        1) сотрудник подразделения, у которого есть подчинённые внутри него;
        2) сотрудник подразделения, у которого есть подчинённые в дочерних отделах;
        3) внешний руководитель, если ему подчиняется большинство сотрудников;
        4) единственный сотрудник, отчитывающийся за пределы подразделения.
        """
        for department in Department.objects.filter(is_active=True):
            staff = list(
                Employee.objects.filter(department=department, is_active=True).only(
                    "pk", "manager_id", "last_name"
                )
            )
            if not staff:
                continue
            staff_ids = {person.pk for person in staff}
            head: Employee | None = None

            internal_with_team = (
                Employee.objects.filter(
                    department=department,
                    is_active=True,
                    subordinates__is_active=True,
                    subordinates__department=department,
                )
                .annotate(
                    team_size=Count(
                        "subordinates",
                        filter=Q(subordinates__department=department, subordinates__is_active=True),
                    )
                )
                .order_by("-team_size", "last_name", "first_name")
            )
            candidates = list(internal_with_team)
            if candidates:
                head = candidates[0]
            else:
                # сотрудник подразделения, ведущий дочерние отделы
                leads_child_units = (
                    Employee.objects.filter(
                        department=department, is_active=True, subordinates__is_active=True
                    )
                    .annotate(team_size=Count("subordinates", filter=Q(subordinates__is_active=True)))
                    .order_by("-team_size", "last_name")
                )
                leads = list(leads_child_units)
                if len(staff) == 1:
                    head = staff[0] if (staff[0].manager_id is None or staff[0].manager_id not in staff_ids) else None
                elif leads:
                    reporting_out = [
                        person
                        for person in leads
                        if person.manager_id is None or person.manager_id not in staff_ids
                    ]
                    head = (reporting_out or leads)[0]
                elif len(staff) >= 2:
                    external = (
                        Employee.objects.filter(
                            is_active=True, subordinates__department=department, subordinates__is_active=True
                        )
                        .exclude(department=department)
                        .annotate(
                            team_size=Count(
                                "subordinates",
                                filter=Q(subordinates__department=department, subordinates__is_active=True),
                            )
                        )
                        .order_by("-team_size", "last_name")
                        .first()
                    )
                    if external is not None and len(staff) >= 2:
                        head = external
                else:
                    outside = [
                        person
                        for person in staff
                        if person.manager_id is None or person.manager_id not in staff_ids
                    ]
                    head = outside[0] if len(outside) == 1 else None

            if head is not None and department.head_id != head.pk:
                department.head = head
                department.save(update_fields=["head", "updated_at"])
                self.report.department_heads += 1

    # -- роли -------------------------------------------------------------
    def _assign_roles_for(self, employees: list[Employee]) -> None:
        roles = list(Role.objects.filter(is_active=True).exclude(ad_groups=[]))
        if not roles or not employees:
            return
        for role in roles:
            patterns = [p for p in (role.ad_groups or []) if isinstance(p, str) and p.strip()]
            if not patterns:
                continue
            matched_ids = []
            for employee in employees:
                if self._matches_groups(patterns, employee.ad_groups or [], employee.ad_group_dns or []):
                    matched_ids.append(employee.pk)
            if matched_ids:
                role.members.add(*matched_ids)
                self.report.roles_assigned += len(matched_ids)

    @staticmethod
    def _matches_groups(patterns: list[str], names: list[str], dns: list[str]) -> bool:
        candidates = [n.lower() for n in names] + [d.lower() for d in dns]
        for pattern in patterns:
            needle = pattern.strip().lower()
            if not needle:
                continue
            for candidate in candidates:
                if candidate == needle:
                    return True
                if any(ch in needle for ch in "*?") and fnmatch.fnmatch(candidate, needle):
                    return True
                # DN может содержать подгруппы/лишние OU — сравниваем и по вхождению
                if needle.startswith("cn=") and needle in candidate:
                    return True
        return False

    def _sync_groups(self, groups) -> None:
        if not groups:
            return
        now = timezone.now()
        for record in groups:
            AdGroup.objects.update_or_create(
                dn=record.dn,
                defaults={
                    "name": record.name[:255],
                    "description": record.description[:512],
                    "member_count": record.member_count,
                    "seen_at": now,
                },
            )

    # -- деактивация ------------------------------------------------------
    def _deactivate_missing(self, present_usernames: set[str]) -> None:
        missing = (
            Employee.objects.filter(source=Employee.Source.AD, is_active=True, missing_in_ad=False)
            .exclude(username__in=present_usernames)
        )
        for employee in missing:
            if employee.is_superuser:
                continue
            employee.is_active = False
            employee.missing_in_ad = True
            employee.save(update_fields=["is_active", "missing_in_ad", "updated_at"])
            self.report.deactivated += 1
            AuditEvent.record(
                "employee.deactivated_by_sync",
                actor=self.actor,
                target=employee,
                payload={"reason": "учётная запись отсутствует в AD"},
            )

    # -- протокол ---------------------------------------------------------
    def _finish(self, run: AdSyncRun, started, dry_run: bool = False) -> AdSyncRun:
        run.finished_at = timezone.now()
        counters = self.report.as_dict()
        status = AdSyncRun.Status.SUCCESS
        if self.report.errors:
            status = AdSyncRun.Status.PARTIAL
        if not dry_run and self.report.created == 0 and self.report.updated == 0 and self.report.errors:
            status = AdSyncRun.Status.FAILED
        run.status = status
        run.counters = counters
        run.message = (
            "Проверка выполнена без записи изменений"
            if dry_run
            else f"Создано: {self.report.created}, обновлено: {self.report.updated}, "
            f"деактивировано: {self.report.deactivated}"
        )
        run.log_excerpt = "\n".join(
            [f"WARN: {w}" for w in self.report.warnings[:20]] + [f"ERROR: {e}" for e in self.report.errors[:20]]
        )[:20000]
        run.save()

        if not dry_run:
            AuditEvent.record(
                "ad.sync_completed",
                actor=self.actor,
                target=run,
                payload=counters,
                request=self.request,
            )
        return run

    def _fail(self, run: AdSyncRun, message: str, started=None) -> AdSyncRun:
        run.finished_at = timezone.now()
        run.status = AdSyncRun.Status.FAILED
        run.message = message[:500]
        run.counters = self.report.as_dict()
        run.log_excerpt = (run.log_excerpt + "\n" + message)[:20000]
        run.save()
        AuditEvent.record(
            "ad.sync_failed",
            actor=self.actor,
            target=run,
            payload={"message": message[:500]},
            request=self.request,
        )
        return run


def run_sync(*, since: datetime | None = None, **kwargs) -> AdSyncRun:
    """Удобная обёртка для management-команд и планировщика."""
    return AdSyncService(**kwargs).run(since=since)
