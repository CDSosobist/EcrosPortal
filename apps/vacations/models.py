"""Модели графика отпусков: периоды, права на дни, маршруты и заявки."""
from __future__ import annotations

from django.db import models
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Department, Employee, Role


class SchedulePeriod(models.Model):
    """Кампания «график отпусков на год»."""

    class Status(models.TextChoices):
        COLLECTING = "collecting", "Сбор заявлений"
        REVIEW = "review", "Согласование и формирование"
        APPROVED = "approved", "График утверждён"
        CLOSED = "closed", "Закрыт"

    year = models.PositiveSmallIntegerField("Год", unique=True)
    status = models.CharField(
        "Статус", max_length=20, choices=Status.choices, default=Status.COLLECTING
    )
    submission_deadline = models.DateField("Приём заявлений до", null=True, blank=True)
    note = models.TextField("Примечание", blank=True)
    published_at = models.DateTimeField("Утверждён", null=True, blank=True)
    published_by = models.ForeignKey(
        Employee,
        verbose_name="Утвердил",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="published_schedules",
    )
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        verbose_name = "Период графика отпусков"
        verbose_name_plural = "Периоды графиков отпусков"
        ordering = ("-year",)

    def __str__(self) -> str:
        return f"График отпусков {self.year}"

    @property
    def accepts_requests(self) -> bool:
        if self.status in {self.Status.APPROVED, self.Status.CLOSED}:
            return False
        if self.submission_deadline and timezone.localdate() > self.submission_deadline:
            return False
        return True

    @property
    def approved_count(self) -> int:
        return self.requests.filter(status=VacationRequest.Status.APPROVED).count()


class VacationType(models.Model):
    """Вид отпуска: ежегодный, дополнительный, без сохранения заработной платы и т.д."""

    code = models.SlugField("Код", max_length=40, unique=True)
    name = models.CharField("Название", max_length=120)
    default_days = models.PositiveSmallIntegerField("Дней в год по умолчанию", default=0)
    consumes_balance = models.BooleanField(
        "Списывает дни с баланса", default=True,
        help_text="Для отпуска без сохранения заработной платы обычно выключается.",
    )
    requires_approval = models.BooleanField("Требует согласования", default=True)
    color = models.CharField("Цвет в календаре", max_length=20, default="#3b82f6")
    is_active = models.BooleanField("Активен", default=True)
    sort_order = models.PositiveSmallIntegerField("Порядок", default=100)

    class Meta:
        verbose_name = "Вид отпуска"
        verbose_name_plural = "Виды отпусков"
        ordering = ("sort_order", "name")

    def __str__(self) -> str:
        return self.name


class VacationBalance(models.Model):
    """Право сотрудника на оплачиваемые дни отпуска в году."""

    employee = models.ForeignKey(
        Employee, verbose_name="Сотрудник", on_delete=models.CASCADE, related_name="vacation_balances"
    )
    year = models.PositiveSmallIntegerField("Год")
    entitled_days = models.PositiveSmallIntegerField("Положено дней", default=28)
    carried_over_days = models.PositiveSmallIntegerField("Перенесено с прошлого года", default=0)
    note = models.CharField("Примечание", max_length=255, blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Право на отпуск"
        verbose_name_plural = "Права на отпуск"
        ordering = ("-year", "employee__last_name")
        constraints = [
            models.UniqueConstraint(fields=["employee", "year"], name="uniq_balance_employee_year")
        ]

    def __str__(self) -> str:
        return f"{self.employee.short_name} — {self.year}: {self.entitled_days} дн."

    @property
    def total_days(self) -> int:
        return self.entitled_days + self.carried_over_days

    def used_days(self, exclude_request_id: int | None = None) -> int:
        qs = VacationRequest.objects.filter(
            employee=self.employee,
            period__year=self.year,
            status=VacationRequest.Status.APPROVED,
            vacation_type__consumes_balance=True,
        )
        if exclude_request_id:
            qs = qs.exclude(pk=exclude_request_id)
        return qs.aggregate(total=models.Sum("days_count"))["total"] or 0

    def pending_days(self, exclude_request_id: int | None = None) -> int:
        qs = VacationRequest.objects.filter(
            employee=self.employee,
            period__year=self.year,
            status=VacationRequest.Status.PENDING,
            vacation_type__consumes_balance=True,
        )
        if exclude_request_id:
            qs = qs.exclude(pk=exclude_request_id)
        return qs.aggregate(total=models.Sum("days_count"))["total"] or 0

    def available_days(self, extra_days: int = 0) -> int:
        return self.total_days - self.used_days() - self.pending_days() - extra_days


class ApprovalChainTemplate(models.Model):
    """Маршрут согласования отпусков.

    Маршрут выбирается по приоритету: подразделение заявителя, затем роль
    заявителя, затем маршрут по умолчанию.
    """

    code = models.SlugField("Код", max_length=60, unique=True)
    name = models.CharField("Название", max_length=150)
    description = models.TextField("Описание", blank=True)
    department = models.ForeignKey(
        Department,
        verbose_name="Подразделение",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="approval_chains",
        help_text="Если задано — маршрут применяется к сотрудникам подразделения (включая вложенные).",
    )
    applies_to_roles = models.ManyToManyField(
        Role,
        verbose_name="Роли заявителя",
        blank=True,
        related_name="approval_chains",
        help_text="Маршрут для сотрудников с указанными ролями.",
    )
    vacation_types = models.ManyToManyField(
        VacationType,
        verbose_name="Виды отпуска",
        blank=True,
        related_name="approval_chains",
        help_text="Пусто — маршрут применяется ко всем видам отпуска.",
    )
    is_default = models.BooleanField("Маршрут по умолчанию", default=False)
    is_active = models.BooleanField("Активен", default=True)
    priority = models.PositiveSmallIntegerField("Приоритет", default=100, help_text="Меньше — важнее.")
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Маршрут согласования"
        verbose_name_plural = "Маршруты согласования"
        ordering = ("priority", "name")

    def __str__(self) -> str:
        return self.name

    @property
    def step_count(self) -> int:
        return self.steps.count()

    def ordered_steps(self):
        return self.steps.select_related("role", "user").order_by("order", "id")


class ApprovalChainStep(models.Model):
    """Шаг маршрута: кто именно согласует и обязателен ли он."""

    class ApproverKind(models.TextChoices):
        MANAGER = "manager", "Непосредственный руководитель"
        MANAGER_2 = "manager_2", "Руководитель руководителя"
        DEPARTMENT_HEAD = "department_head", "Руководитель подразделения"
        DIRECTOR = "director", "Директор направления"
        HR = "hr", "Отдел кадров"
        ROLE = "role", "Сотрудник с ролью"
        USER = "user", "Конкретный сотрудник"

    template = models.ForeignKey(
        ApprovalChainTemplate, verbose_name="Маршрут", on_delete=models.CASCADE, related_name="steps"
    )
    order = models.PositiveSmallIntegerField("Порядок", default=1)
    title = models.CharField("Название шага", max_length=120, blank=True)
    approver_kind = models.CharField(
        "Тип согласующего", max_length=30, choices=ApproverKind.choices, default=ApproverKind.MANAGER
    )
    role = models.ForeignKey(
        Role, verbose_name="Роль", null=True, blank=True, on_delete=models.SET_NULL
    )
    user = models.ForeignKey(
        Employee, verbose_name="Сотрудник", null=True, blank=True, on_delete=models.SET_NULL
    )
    skip_if_missing = models.BooleanField(
        "Пропускать шаг, если согласующий не найден", default=True,
        help_text="Если выключено, заявление будет остановлено с ошибкой при отсутствии согласующего.",
    )

    class Meta:
        verbose_name = "Шаг маршрута"
        verbose_name_plural = "Шаги маршрута"
        ordering = ("template", "order", "id")

    def __str__(self) -> str:
        return f"{self.order}. {self.title or self.get_approver_kind_display()}"

    @property
    def display_title(self) -> str:
        if self.title:
            return self.title
        if self.approver_kind == self.ApproverKind.ROLE and self.role_id:
            return f"Согласование: {self.role.name}"
        if self.approver_kind == self.ApproverKind.USER and self.user_id:
            return f"Согласование: {self.user.short_name}"
        return self.get_approver_kind_display()


class VacationRequest(models.Model):
    """Заявление на отпуск и его движение по маршруту согласования."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        PENDING = "pending", "На согласовании"
        APPROVED = "approved", "Согласовано"
        REJECTED = "rejected", "Отклонено"
        CANCELLED = "cancelled", "Отозвано"

    employee = models.ForeignKey(
        Employee, verbose_name="Сотрудник", on_delete=models.CASCADE, related_name="vacation_requests"
    )
    period = models.ForeignKey(
        SchedulePeriod, verbose_name="Период", on_delete=models.PROTECT, related_name="requests"
    )
    vacation_type = models.ForeignKey(
        VacationType, verbose_name="Вид отпуска", on_delete=models.PROTECT, related_name="requests"
    )
    start_date = models.DateField("Дата начала")
    end_date = models.DateField("Дата окончания")
    days_count = models.PositiveSmallIntegerField("Календарных дней", default=0)
    status = models.CharField(
        "Статус", max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    chain_template = models.ForeignKey(
        ApprovalChainTemplate,
        verbose_name="Маршрут",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="requests",
    )
    employee_comment = models.TextField("Комментарий сотрудника", blank=True)
    decision_comment = models.TextField("Решение согласующего", blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)
    submitted_at = models.DateTimeField("Отправлено", null=True, blank=True)
    decided_at = models.DateTimeField("Решение принято", null=True, blank=True)
    cancelled_at = models.DateTimeField("Отозвано", null=True, blank=True)
    cancelled_by = models.ForeignKey(
        Employee,
        verbose_name="Отозвал",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cancelled_vacation_requests",
    )

    class Meta:
        verbose_name = "Заявление на отпуск"
        verbose_name_plural = "Заявления на отпуск"
        ordering = ("start_date", "employee__last_name")
        indexes = [
            models.Index(fields=["status", "start_date"]),
            models.Index(fields=["employee", "status"]),
        ]

    def __str__(self) -> str:
        return f"№{self.pk} {self.employee.short_name}: {self.start_date:%d.%m.%Y}–{self.end_date:%d.%m.%Y}"

    def get_absolute_url(self) -> str:
        return reverse("vacations:request_detail", args=[self.pk])

    @property
    def number(self) -> str:
        return f"ОТ-{self.period.year}-{self.pk:04d}" if self.pk else "—"

    @property
    def is_editable(self) -> bool:
        return self.status in {self.Status.DRAFT, self.Status.REJECTED}

    @property
    def is_pending(self) -> bool:
        return self.status == self.Status.PENDING

    @property
    def is_approved(self) -> bool:
        return self.status == self.Status.APPROVED

    @property
    def is_final(self) -> bool:
        return self.status in {self.Status.APPROVED, self.Status.REJECTED, self.Status.CANCELLED}

    def steps_ordered(self):
        return self.steps.select_related("approver", "acted_by").order_by("order", "id")

    @property
    def current_step(self) -> "VacationRequestStep | None":
        if self.status != self.Status.PENDING:
            return None
        return self.steps.filter(status=VacationRequestStep.Status.PENDING).order_by("order").first()

    @property
    def route_total(self) -> int:
        return self.steps.count()

    @property
    def route_done(self) -> int:
        return self.steps.filter(
            status__in=[VacationRequestStep.Status.APPROVED, VacationRequestStep.Status.SKIPPED]
        ).count()

    @property
    def route_progress(self) -> int:
        total = self.route_total
        return int(self.route_done * 100 / total) if total else 0

    @property
    def overlaps_other_requests(self):
        """Пересечения с согласованными отпусками коллег по подразделению."""
        if not self.employee.department_id:
            return VacationRequest.objects.none()
        return (
            VacationRequest.objects.filter(
                employee__department_id=self.employee.department_id,
                status=self.Status.APPROVED,
                start_date__lte=self.end_date,
                end_date__gte=self.start_date,
            )
            .exclude(employee=self.employee)
            .select_related("employee")
        )


class VacationRequestStep(models.Model):
    """Зафиксированный шаг согласования конкретного заявления.

    Маршрут «замораживается» при отправке: последующие правки шаблона не
    переписывают историю уже согласуемых заявлений.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает решения"
        APPROVED = "approved", "Согласовано"
        REJECTED = "rejected", "Отклонено"
        SKIPPED = "skipped", "Пропущено"
        CANCELLED = "cancelled", "Отменено"

    request = models.ForeignKey(
        VacationRequest, verbose_name="Заявление", on_delete=models.CASCADE, related_name="steps"
    )
    order = models.PositiveSmallIntegerField("Порядок", default=1)
    title = models.CharField("Шаг", max_length=150)
    approver_kind = models.CharField("Тип согласующего", max_length=30, blank=True)
    approver = models.ForeignKey(
        Employee,
        verbose_name="Согласующий",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="vacation_approval_steps",
    )
    approver_label = models.CharField("Согласующий (текст)", max_length=255, blank=True)
    status = models.CharField(
        "Статус", max_length=20, choices=Status.choices, default=Status.PENDING
    )
    comment = models.TextField("Комментарий", blank=True)
    acted_by = models.ForeignKey(
        Employee,
        verbose_name="Решение принял",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="acted_vacation_steps",
    )
    decided_at = models.DateTimeField("Решение принято", null=True, blank=True)
    notified_at = models.DateTimeField("Уведомление отправлено", null=True, blank=True)

    class Meta:
        verbose_name = "Шаг согласования"
        verbose_name_plural = "Шаги согласования"
        ordering = ("request", "order", "id")
        indexes = [models.Index(fields=["approver", "status"])]

    def __str__(self) -> str:
        return f"{self.title} — {self.get_status_display()}"

    @property
    def is_actionable(self) -> bool:
        return self.status == self.Status.PENDING and self.approver_id is not None


class Holiday(models.Model):
    """Производственный календарь: нерабочие праздничные дни."""

    date = models.DateField("Дата", unique=True)
    name = models.CharField("Название", max_length=120)
    is_working = models.BooleanField(
        "Рабочий день (перенос)", default=False,
        help_text="Отмечает перенесённые рабочие субботы — они не исключаются из отпуска.",
    )

    class Meta:
        verbose_name = "Праздничный день"
        verbose_name_plural = "Производственный календарь"
        ordering = ("date",)
        indexes = [models.Index(fields=["date"])]

    def __str__(self) -> str:
        return f"{self.date:%d.%m.%Y} — {self.name}"
