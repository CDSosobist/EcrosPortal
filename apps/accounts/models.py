"""Модели предметной области «Сотрудники».

Здесь живут:
  * оргструктура (Department) и её руководители;
  * учётная запись сотрудника (Employee) — она же AUTH_USER_MODEL;
  * портальные роли (Role) и их сопоставление с группами AD;
  * журнал действий (AuditEvent) и протоколы синхронизации (AdSyncRun).
"""
from __future__ import annotations

import hashlib

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class Department(models.Model):
    """Подразделение компании. Дерево произвольной глубины."""

    name = models.CharField("Название", max_length=200)
    code = models.SlugField("Код", max_length=80, unique=True)
    short_name = models.CharField("Короткое название", max_length=80, blank=True)
    parent = models.ForeignKey(
        "self",
        verbose_name="Вышестоящее подразделение",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children",
    )
    head = models.ForeignKey(
        "accounts.Employee",
        verbose_name="Руководитель",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="headed_departments",
    )
    ad_dn = models.CharField(
        "DN в Active Directory", max_length=512, blank=True, default="", db_index=True
    )
    sort_order = models.PositiveIntegerField("Порядок сортировки", default=100)
    is_active = models.BooleanField("Действует", default=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Подразделение"
        verbose_name_plural = "Подразделения"
        ordering = ("sort_order", "name")
        indexes = [models.Index(fields=["parent", "name"])]

    def __str__(self) -> str:
        return self.name

    def get_ancestors(self) -> list["Department"]:
        chain: list[Department] = []
        node = self.parent
        seen: set[int] = set()
        while node is not None and node.pk not in seen:
            seen.add(node.pk)
            chain.append(node)
            node = node.parent
        chain.reverse()
        return chain

    def get_descendants(self, include_self: bool = False) -> list["Department"]:
        result: list[Department] = [self] if include_self else []
        frontier = list(self.children.all())
        while frontier:
            node = frontier.pop(0)
            result.append(node)
            frontier.extend(node.children.all())
        return result

    def descendant_ids(self, include_self: bool = True) -> list[int]:
        ids = [d.pk for d in self.get_descendants(include_self=include_self)]
        return [i for i in ids if i is not None]

    @property
    def full_path(self) -> str:
        return " / ".join([d.name for d in self.get_ancestors()] + [self.name])

    @property
    def head_or_parent_head(self) -> "Employee | None":
        """Руководитель подразделения; если не назначен — руководитель выше."""
        node: Department | None = self
        seen: set[int] = set()
        while node is not None and node.pk not in seen:
            seen.add(node.pk)
            if node.head_id:
                return node.head
            node = node.parent
        return None


class EmployeeQuerySet(models.QuerySet):
    def active(self) -> "EmployeeQuerySet":
        return self.filter(is_active=True, status=Employee.Status.ACTIVE)

    def with_org(self) -> "EmployeeQuerySet":
        return self.select_related("department", "manager")


class EmployeeManager(BaseUserManager):
    use_in_migrations = True
    _queryset_class = EmployeeQuerySet

    def _create(self, username: str, password: str | None, **extra):
        if not username:
            raise ValueError("Не задан логин сотрудника")
        email = extra.pop("email", "") or ""
        extra.setdefault("display_name", username)
        user = self.model(username=username.strip().lower(), email=email, **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, username: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(username, password, **extra)

    def create_superuser(self, username: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("status", Employee.Status.ACTIVE)
        extra.setdefault("display_name", extra.get("display_name") or username)
        return self._create(username, password, **extra)

    def active(self) -> "EmployeeQuerySet":
        return self.get_queryset().active()


class Employee(AbstractBaseUser, PermissionsMixin):
    """Сотрудник компании. Учётная запись создаётся синхронизацией с AD."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Работает"
        ON_VACATION = "vacation", "В отпуске"
        ON_LEAVE = "leave", "Отсутствует"
        DISMISSED = "dismissed", "Уволен"

    class Source(models.TextChoices):
        AD = "ad", "Active Directory"
        LOCAL = "local", "Локальная учётная запись"

    # --- учётные данные ---
    username = models.CharField("Логин (sAMAccountName)", max_length=150, unique=True)
    email = models.EmailField("Электронная почта", blank=True, default="", db_index=True)
    password = models.CharField("Пароль", max_length=128, blank=True, default="")

    # --- персональные данные ---
    last_name = models.CharField("Фамилия", max_length=120, blank=True)
    first_name = models.CharField("Имя", max_length=120, blank=True)
    middle_name = models.CharField("Отчество", max_length=120, blank=True)
    display_name = models.CharField("ФИО (как в AD)", max_length=255, blank=True)
    job_title = models.CharField("Должность", max_length=200, blank=True)
    employee_no = models.CharField("Табельный номер", max_length=40, blank=True, db_index=True)

    # --- оргструктура ---
    department = models.ForeignKey(
        Department,
        verbose_name="Подразделение",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="employees",
    )
    manager = models.ForeignKey(
        "self",
        verbose_name="Руководитель",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="subordinates",
    )

    # --- контакты ---
    phone_internal = models.CharField("Внутренний телефон", max_length=40, blank=True)
    phone_mobile = models.CharField("Мобильный телефон", max_length=40, blank=True)
    office_room = models.CharField("Кабинет", max_length=60, blank=True)
    city = models.CharField("Город", max_length=80, blank=True)

    # --- кадровые данные ---
    hire_date = models.DateField("Дата приёма", null=True, blank=True)
    dismissal_date = models.DateField("Дата увольнения", null=True, blank=True)
    status = models.CharField(
        "Статус", max_length=20, choices=Status.choices, default=Status.ACTIVE
    )
    source = models.CharField(
        "Источник", max_length=20, choices=Source.choices, default=Source.AD
    )
    position_grade = models.CharField("Грейд/категория", max_length=60, blank=True)

    # --- синхронизация с AD ---
    ad_dn = models.CharField("DN в Active Directory", max_length=512, blank=True, default="")
    ad_guid = models.CharField(
        "objectGUID", max_length=64, blank=True, default="", db_index=True
    )
    ad_groups = models.JSONField("Группы AD", default=list, blank=True)
    ad_group_dns = models.JSONField("DN групп AD", default=list, blank=True)
    ad_synced_at = models.DateTimeField("Синхронизировано", null=True, blank=True)
    ad_when_changed = models.DateTimeField("whenChanged в AD", null=True, blank=True)
    missing_in_ad = models.BooleanField(
        "Отсутствует в AD", default=False,
        help_text="Учётная запись перестала приходить при синхронизации.",
    )

    # --- служебные флаги Django ---
    is_active = models.BooleanField("Активна", default=True)
    is_staff = models.BooleanField("Доступ в админку", default=False)

    last_login_ip = models.GenericIPAddressField("IP последнего входа", null=True, blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    objects = EmployeeManager()

    USERNAME_FIELD = "username"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        verbose_name = "Сотрудник"
        verbose_name_plural = "Сотрудники"
        ordering = ("last_name", "first_name", "username")
        indexes = [
            models.Index(fields=["department", "status"]),
            models.Index(fields=["last_name", "first_name"]),
        ]

    def __str__(self) -> str:
        return f"{self.short_name} ({self.username})"

    # --- отображаемые имена -------------------------------------------------
    @property
    def full_name(self) -> str:
        parts = [self.last_name, self.first_name, self.middle_name]
        built = " ".join(p for p in parts if p).strip()
        return built or self.display_name or self.username

    @property
    def short_name(self) -> str:
        if self.last_name:
            tail = f"{self.first_name[:1]}.{self.middle_name[:1]}." if self.first_name else ""
            return f"{self.last_name} {tail}".strip()
        return self.display_name or self.username

    @property
    def initials(self) -> str:
        if self.last_name and self.first_name:
            return f"{self.last_name[0]}{self.first_name[0]}".upper()
        words = (self.display_name or self.username).split()
        return "".join(w[0] for w in words[:2]).upper() or "?"

    @property
    def avatar_color(self) -> int:
        digest = hashlib.md5(self.username.encode("utf-8")).hexdigest()
        return int(digest[:2], 16) % 8 + 1

    @property
    def is_dismissed(self) -> bool:
        return self.status == self.Status.DISMISSED

    @property
    def weekday_status(self) -> str:
        """Человекочитаемый статус для справочника."""
        if self.is_dismissed:
            return "Уволен"
        return self.get_status_display()

    # --- роли и права -------------------------------------------------------
    @property
    def role_codes(self) -> set[str]:
        return set(self.roles.values_list("code", flat=True))

    def has_role(self, *codes: str) -> bool:
        if not codes:
            return False
        if self.is_superuser:
            return True
        return bool(self.role_codes & set(codes))

    @property
    def is_hr(self) -> bool:
        return self.has_role("hr")

    @property
    def is_portal_admin(self) -> bool:
        return self.is_superuser or self.is_staff or self.has_role("admin")

    @property
    def is_manager(self) -> bool:
        return self.subordinates.filter(is_active=True).exists()

    def manager_chain(self, max_depth: int = 10) -> list["Employee"]:
        """Цепочка руководителей вверх по иерархии."""
        chain: list[Employee] = []
        node = self.manager
        seen: set[int] = {self.pk} if self.pk else set()
        while node is not None and node.pk not in seen and len(chain) < max_depth:
            seen.add(node.pk)
            chain.append(node)
            node = node.manager
        return chain

    def subordinates_tree(self, depth: int = 3) -> list["Employee"]:
        result: list[Employee] = []
        frontier = [(s, 1) for s in self.subordinates.filter(is_active=True)]
        while frontier:
            node, level = frontier.pop(0)
            result.append(node)
            if level < depth:
                frontier.extend((s, level + 1) for s in node.subordinates.filter(is_active=True))
        return result

    # --- прочее -------------------------------------------------------------
    def get_short_name(self) -> str:
        return self.first_name or self.username

    def get_full_name(self) -> str:
        return self.full_name

    def get_absolute_url(self) -> str:
        from django.urls import reverse

        return reverse("portal:employee_detail", args=[self.pk])

    def touch_last_login(self, ip: str | None) -> None:
        self.last_login_ip = ip
        self.last_login = timezone.now()
        self.save(update_fields=["last_login_ip", "last_login"])

    def as_directory_row(self) -> dict:
        return {
            "id": self.pk,
            "full_name": self.full_name,
            "job_title": self.job_title,
            "department": self.department.name if self.department else "",
            "email": self.email,
        }


class Role(models.Model):
    """Портальная роль. Сопоставляется с группами AD по маскам имён."""

    code = models.SlugField("Код", max_length=60, unique=True)
    name = models.CharField("Название", max_length=120)
    description = models.TextField("Описание", blank=True)
    ad_groups = models.JSONField(
        "Группы AD",
        default=list,
        blank=True,
        help_text=(
            "Список имён или DN групп Active Directory. Поддерживаются шаблоны: "
            "«ECROS-Portal-HR», «CN=Portal-HR,OU=Groups,DC=ecros,DC=local», «ECROS-*-HR»."
        ),
    )
    head = models.ForeignKey(
        Employee,
        verbose_name="Ответственный по роли",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="responsible_roles",
        help_text="Кому попадают задачи согласования по этой роли.",
    )
    is_system = models.BooleanField("Системная роль", default=False)
    is_active = models.BooleanField("Активна", default=True)
    sort_order = models.PositiveIntegerField("Порядок", default=100)
    members = models.ManyToManyField(
        Employee, verbose_name="Сотрудники", related_name="roles", blank=True
    )

    class Meta:
        verbose_name = "Роль"
        verbose_name_plural = "Роли"
        ordering = ("sort_order", "name")

    def __str__(self) -> str:
        return self.name

    @property
    def approver(self) -> "Employee | None":
        """Ответственный за согласование: явно заданный или первый участник."""
        if self.head_id and self.head and self.head.is_active:
            return self.head
        return (
            self.members.filter(is_active=True, status=Employee.Status.ACTIVE)
            .order_by("last_name", "first_name")
            .first()
        )


class AuditEvent(models.Model):
    """Журнал значимых действий: кто, что, когда и с какими данными."""

    created_at = models.DateTimeField("Время", default=timezone.now, db_index=True)
    actor = models.ForeignKey(
        Employee,
        verbose_name="Автор",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    actor_name = models.CharField("Автор (текст)", max_length=255, blank=True)
    action = models.CharField("Действие", max_length=120, db_index=True)
    target_type = models.CharField("Тип объекта", max_length=80, blank=True)
    target_id = models.CharField("Идентификатор объекта", max_length=80, blank=True)
    target_label = models.CharField("Объект", max_length=255, blank=True)
    payload = models.JSONField("Данные", default=dict, blank=True)
    ip_address = models.GenericIPAddressField("IP", null=True, blank=True)

    class Meta:
        verbose_name = "Событие журнала"
        verbose_name_plural = "Журнал действий"
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["action", "created_at"])]

    def __str__(self) -> str:
        return f"{self.created_at:%d.%m.%Y %H:%M} — {self.action}"

    @classmethod
    def record(
        cls,
        action: str,
        *,
        actor: Employee | None = None,
        target=None,
        target_label: str = "",
        payload: dict | None = None,
        request=None,
    ) -> "AuditEvent":
        ip = None
        if request is not None:
            ip = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get(
                "REMOTE_ADDR"
            )
        actor_name = ""
        if actor is not None:
            actor_name = actor.full_name if actor.pk else str(actor)
        target_type = target.__class__.__name__ if target is not None else ""
        target_id = str(getattr(target, "pk", "") or "")
        if not target_label and target is not None:
            target_label = str(target)[:255]
        return cls.objects.create(
            action=action,
            actor=actor if getattr(actor, "pk", None) else None,
            actor_name=actor_name,
            target_type=target_type,
            target_id=target_id,
            target_label=target_label[:255],
            payload=payload or {},
            ip_address=ip or None,
        )


class AdSyncRun(models.Model):
    """Протокол одного запуска синхронизации с Active Directory."""

    class Status(models.TextChoices):
        RUNNING = "running", "Выполняется"
        SUCCESS = "success", "Успешно"
        PARTIAL = "partial", "С предупреждениями"
        FAILED = "failed", "Ошибка"

    class Mode(models.TextChoices):
        FULL = "full", "Полная"
        INCREMENTAL = "incremental", "Инкрементальная"
        DRY_RUN = "dry_run", "Проверка (без записи)"

    started_at = models.DateTimeField("Начало", default=timezone.now)
    finished_at = models.DateTimeField("Окончание", null=True, blank=True)
    provider = models.CharField("Источник", max_length=20, default="ldap")
    mode = models.CharField("Режим", max_length=20, choices=Mode.choices, default=Mode.FULL)
    status = models.CharField(
        "Статус", max_length=20, choices=Status.choices, default=Status.RUNNING
    )
    counters = models.JSONField("Счётчики", default=dict, blank=True)
    message = models.TextField("Сообщение", blank=True)
    log_excerpt = models.TextField("Журнал", blank=True)
    triggered_by = models.ForeignKey(
        Employee,
        verbose_name="Запустил",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ad_sync_runs",
    )

    class Meta:
        verbose_name = "Синхронизация с AD"
        verbose_name_plural = "Синхронизации с AD"
        ordering = ("-started_at",)

    def __str__(self) -> str:
        return f"{self.started_at:%d.%m.%Y %H:%M} — {self.get_status_display()}"

    @property
    def duration_seconds(self) -> float | None:
        if not self.finished_at:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def is_failed(self) -> bool:
        return self.status == self.Status.FAILED


class AdGroup(models.Model):
    """Справочник групп AD, увиденных при синхронизации (для настройки ролей)."""

    dn = models.CharField("DN", max_length=512, unique=True)
    name = models.CharField("Имя группы", max_length=255, db_index=True)
    description = models.CharField("Описание", max_length=512, blank=True)
    member_count = models.PositiveIntegerField("Участников", default=0)
    seen_at = models.DateTimeField("Замечена", default=timezone.now)

    class Meta:
        verbose_name = "Группа AD"
        verbose_name_plural = "Группы AD"
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name
