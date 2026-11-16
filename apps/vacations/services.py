"""Бизнес-логика графика отпусков: дни, маршруты, согласование, уведомления."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.accounts.models import AuditEvent, Employee, Role
from apps.portal.models import Notification

from .models import (
    ApprovalChainStep,
    ApprovalChainTemplate,
    Holiday,
    SchedulePeriod,
    VacationBalance,
    VacationRequest,
    VacationRequestStep,
    VacationType,
)

logger = logging.getLogger(__name__)


class VacationError(Exception):
    """Ошибка бизнес-правила с текстом для пользователя."""


# ---------------------------------------------------------------------------
# Календарь и дни
# ---------------------------------------------------------------------------
def holiday_dates(start: date, end: date) -> set[date]:
    if not start or not end or end < start:
        return set()
    return set(
        Holiday.objects.filter(date__gte=start, date__lte=end, is_working=False).values_list(
            "date", flat=True
        )
    )


def count_vacation_days(start: date, end: date) -> int:
    """Календарные дни отпуска без нерабочих праздничных дней (ст. 120 ТК РФ)."""
    if not start or not end or end < start:
        return 0
    total = (end - start).days + 1
    return max(0, total - len(holiday_dates(start, end)))


def count_working_days(start: date, end: date) -> int:
    if not start or not end or end < start:
        return 0
    holidays = holiday_dates(start, end)
    extra_working = set(
        Holiday.objects.filter(
            date__gte=start, date__lte=end, is_working=True
        ).values_list("date", flat=True)
    )
    days = 0
    cursor = start
    while cursor <= end:
        if cursor in extra_working or (cursor.weekday() < 5 and cursor not in holidays):
            days += 1
        cursor += timedelta(days=1)
    return days


# ---------------------------------------------------------------------------
# Баланс дней
# ---------------------------------------------------------------------------
def balance_for(employee: Employee, year: int, *, create: bool = True) -> VacationBalance | None:
    balance = VacationBalance.objects.filter(employee=employee, year=year).first()
    if balance is None and create:
        holidays_left = VacationType.objects.filter(
            consumes_balance=True, is_active=True
        ).order_by("sort_order").first()
        default = (
            settings.VACATION_DEFAULT_DAYS
            if holidays_left is None
            else max(settings.VACATION_DEFAULT_DAYS, holidays_left.default_days)
        )
        balance, _ = VacationBalance.objects.get_or_create(
            employee=employee,
            year=year,
            defaults={"entitled_days": default, "carried_over_days": 0},
        )
    return balance


def ensure_balances_for_period(period: SchedulePeriod) -> int:
    """Создаёт права на отпуск всем активным сотрудникам (перед кампанией)."""
    existing = set(
        VacationBalance.objects.filter(year=period.year).values_list("employee_id", flat=True)
    )
    created = 0
    buffer: list[VacationBalance] = []
    for employee in Employee.objects.active().only("pk"):
        if employee.pk in existing:
            continue
        buffer.append(
            VacationBalance(
                employee_id=employee.pk,
                year=period.year,
                entitled_days=settings.VACATION_DEFAULT_DAYS,
            )
        )
    if buffer:
        VacationBalance.objects.bulk_create(buffer, ignore_conflicts=True)
        created = len(buffer)
    return created


def balance_summary(employee: Employee, year: int) -> dict:
    balance = balance_for(employee, year)
    used = balance.used_days() if balance else 0
    pending = balance.pending_days() if balance else 0
    total = balance.total_days if balance else 0
    return {
        "balance": balance,
        "total": total,
        "used": used,
        "pending": pending,
        "available": max(0, total - used - pending),
        "raw_available": total - used - pending,
    }


# ---------------------------------------------------------------------------
# Выбор и разбор маршрута согласования
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class ResolvedStep:
    order: int
    title: str
    approver_kind: str
    approver: Employee | None
    skip_if_missing: bool = True
    note: str = ""


@dataclass
class ResolvedRoute:
    template: ApprovalChainTemplate | None
    steps: list[ResolvedStep] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def usable_steps(self) -> list[ResolvedStep]:
        return [s for s in self.steps if s.approver is not None]


def select_chain_template(
    employee: Employee, vacation_type: VacationType | None
) -> ApprovalChainTemplate | None:
    """Выбирает наиболее подходящий маршрут: подразделение → роль → по умолчанию."""
    queryset = ApprovalChainTemplate.objects.filter(is_active=True).prefetch_related(
        "steps", "applies_to_roles", "vacation_types"
    )
    department_chain = [employee.department] + employee.department.get_ancestors() if employee.department else []
    department_ids = {d.pk for d in department_chain if d is not None}
    # глубина совпадения: собственное подразделение важнее родительского
    depth_by_id = {d.pk: index for index, d in enumerate(department_chain) if d is not None}
    employee_roles = employee.role_codes

    best: tuple[tuple, ApprovalChainTemplate] | None = None
    for template in queryset:
        types = list(template.vacation_types.all())
        if types and vacation_type is not None and vacation_type not in types:
            continue
        roles = {r.code for r in template.applies_to_roles.all()}

        if template.department_id:
            if template.department_id not in department_ids:
                continue
            score = (0, depth_by_id.get(template.department_id, 99), template.priority)
        elif roles:
            if not (employee_roles & roles):
                continue
            score = (1, 0, template.priority)
        elif template.is_default:
            score = (2, 0, template.priority)
        else:
            continue

        if best is None or score < best[0]:
            best = (score, template)
    return best[1] if best else None


def _active_approver(candidate: Employee | None, applicant: Employee) -> Employee | None:
    if candidate is None:
        return None
    if candidate.pk == applicant.pk:
        return None
    if not candidate.is_active or candidate.status == Employee.Status.DISMISSED:
        return None
    return candidate


def _raise_to_active(candidate: Employee | None, applicant: Employee) -> Employee | None:
    """Поднимается по цепочке руководителей, если согласующий недоступен."""
    node = candidate
    seen: set[int] = {applicant.pk}
    while node is not None and node.pk not in seen:
        seen.add(node.pk)
        if node.is_active and node.status != Employee.Status.DISMISSED:
            return node
        node = node.manager
    return None


def _role_approver_in_department(role_code: str, department_id: int | None) -> Employee | None:
    """Согласующий по роли: сначала ответственный по роли, затем сотрудник подразделения."""
    role = Role.objects.filter(code=role_code).first()
    if role is not None and role.head_id and role.head and role.head.is_active:
        return role.head
    qs = Employee.objects.filter(roles__code=role_code, is_active=True)
    if department_id:
        found = qs.filter(department_id=department_id).order_by("last_name").first()
        if found:
            return found
    found = qs.order_by("last_name").first()
    if found:
        return found
    return role.approver if role else None


def _resolve_approver(
    step: ApprovalChainStep, applicant: Employee
) -> tuple[Employee | None, str]:
    kind = step.approver_kind
    department = applicant.department

    if kind == ApprovalChainStep.ApproverKind.MANAGER:
        manager = _raise_to_active(applicant.manager, applicant)
        if manager:
            return manager, ""
        if department is not None:
            head = _raise_to_active(department.head_or_parent_head, applicant)
            if head:
                return head, "непосредственный руководитель не назначен — направлено руководителю подразделения"
        return None, "не удалось определить непосредственного руководителя"

    if kind == ApprovalChainStep.ApproverKind.MANAGER_2:
        chain = applicant.manager_chain()
        if len(chain) >= 2:
            second = _raise_to_active(chain[1], applicant)
            if second:
                return second, ""
            return None, "руководитель руководителя недоступен"
        head = _raise_to_active(department.head_or_parent_head, applicant) if department else None
        if head and head.pk != (chain[0].pk if chain else None):
            return head, "руководитель второго уровня не найден — направлено руководителю подразделения"
        return None, "руководитель второго уровня не найден"

    if kind == ApprovalChainStep.ApproverKind.DEPARTMENT_HEAD:
        head = _raise_to_active(department.head_or_parent_head, applicant) if department else None
        if head:
            return head, ""
        return None, "руководитель подразделения не назначен"

    if kind == ApprovalChainStep.ApproverKind.DIRECTOR:
        for person in applicant.manager_chain():
            if person.has_role("director") and person.is_active:
                return person, ""
        director = _role_approver_in_department("director", None)
        if director and director.pk != applicant.pk:
            return director, ""
        return None, "директор направления не определён"

    if kind == ApprovalChainStep.ApproverKind.HR:
        hr = _role_approver_in_department("hr", applicant.department_id)
        if hr and hr.pk != applicant.pk:
            return hr, ""
        return None, "ответственный сотрудник отдела кадров не определён"

    if kind == ApprovalChainStep.ApproverKind.ROLE:
        if step.role_id is None:
            return None, "в шаге не указана роль"
        approver = step.role.approver
        if approver and approver.pk != applicant.pk:
            return approver, ""
        department_approver = _role_approver_in_department(step.role.code, applicant.department_id)
        if department_approver and department_approver.pk != applicant.pk:
            return department_approver, ""
        return None, f"нет сотрудника с ролью «{step.role.name}»"

    if kind == ApprovalChainStep.ApproverKind.USER:
        if step.user_id is None:
            return None, "в шаге не указан сотрудник"
        if not step.user.is_active:
            return None, f"сотрудник {step.user.short_name} недоступен"
        if step.user.pk == applicant.pk:
            return None, "сотрудник совпадает с заявителем"
        return step.user, ""

    return None, f"неизвестный тип шага: {kind}"


def resolve_route(
    employee: Employee, vacation_type: VacationType | None
) -> ResolvedRoute:
    """Строит маршрут согласования для конкретного заявителя."""
    template = select_chain_template(employee, vacation_type)
    route = ResolvedRoute(template=template)
    if template is None:
        route.problems.append("не настроен ни один маршрут согласования")
        return route

    seen_approvers: set[int] = set()
    order = 0
    for step in template.ordered_steps():
        approver, problem = _resolve_approver(step, employee)
        if approver is not None and approver.pk in seen_approvers:
            approver, problem = None, "согласующий уже участвует в маршруте выше"
        if approver is not None:
            seen_approvers.add(approver.pk)
        elif not step.skip_if_missing:
            route.problems.append(f"{step.display_title}: {problem}")
        order += 1
        route.steps.append(
            ResolvedStep(
                order=order,
                title=step.display_title,
                approver_kind=step.approver_kind,
                approver=approver,
                skip_if_missing=step.skip_if_missing,
                note=problem,
            )
        )
    return route


# ---------------------------------------------------------------------------
# Проверки перед отправкой
# ---------------------------------------------------------------------------
def validate_request(
    employee: Employee,
    period: SchedulePeriod,
    vacation_type: VacationType,
    start_date: date,
    end_date: date,
    *,
    exclude_request_id: int | None = None,
    actor: Employee | None = None,
) -> dict:
    """Проверяет заявление сотрудника и возвращает вычисленный контекст."""
    if end_date < start_date:
        raise VacationError("Дата окончания не может быть раньше даты начала.")
    if period.pk:
        # период мог быть закрыт после открытия страницы — читаем актуальный статус
        period = SchedulePeriod.objects.get(pk=period.pk)
    if not period.accepts_requests:
        raise VacationError(
            f"Приём заявлений в график {period.year} года закрыт. Обратитесь в отдел кадров."
        )
    if start_date.year != period.year:
        raise VacationError(f"Дата начала должна приходиться на {period.year} год.")
    days = count_vacation_days(start_date, end_date)
    if days <= 0:
        raise VacationError("В выбранном периоде нет дней отпуска (все дни — праздничные).")
    if days > settings.VACATION_MAX_DAYS_PER_REQUEST:
        raise VacationError(
            f"За одно заявление можно оформить не более {settings.VACATION_MAX_DAYS_PER_REQUEST} дней."
        )

    today = timezone.localdate()
    is_privileged = actor is not None and (actor.is_hr or actor.is_portal_admin)
    if not is_privileged and not employee.pk == getattr(actor, "pk", employee.pk):
        raise VacationError("Нельзя оформить заявление за другого сотрудника.")

    overlap_qs = VacationRequest.objects.filter(
        employee=employee,
        status__in=[VacationRequest.Status.APPROVED, VacationRequest.Status.PENDING],
        start_date__lte=end_date,
        end_date__gte=start_date,
    )
    if exclude_request_id:
        overlap_qs = overlap_qs.exclude(pk=exclude_request_id)
    overlap = overlap_qs.first()
    if overlap is not None:
        raise VacationError(
            f"Период пересекается с заявлением {overlap.number} "
            f"({overlap.start_date:%d.%m.%Y}–{overlap.end_date:%d.%m.%Y}, "
            f"{overlap.get_status_display().lower()})."
        )

    balance_info = balance_summary(employee, period.year)
    if vacation_type.consumes_balance and days > balance_info["available"]:
        raise VacationError(
            f"Недостаточно дней отпуска: доступно {balance_info['available']}, запрошено {days}. "
            "Проверьте остаток или выберите другой период."
        )

    if not is_privileged:
        notice_days = settings.VACATION_MIN_NOTICE_DAYS
        if (start_date - today).days < notice_days:
            raise VacationError(
                f"До начала отпуска должно оставаться не менее {notice_days} дн. "
                "Срочное оформление — через отдел кадров."
            )

    return {
        "days": days,
        "balance": balance_info,
        "overlaps": team_overlap(employee, start_date, end_date),
    }


def team_overlap(employee: Employee, start_date: date, end_date: date) -> dict:
    """Сколько коллег по подразделению одновременно в отпуске."""
    if not employee.department_id:
        return {"colleagues": 0, "total": 0, "percent": 0, "names": []}
    staff_total = Employee.objects.filter(
        department_id=employee.department_id, is_active=True
    ).count()
    overlapping = (
        VacationRequest.objects.filter(
            employee__department_id=employee.department_id,
            employee__is_active=True,
            status=VacationRequest.Status.APPROVED,
            start_date__lte=end_date,
            end_date__gte=start_date,
        )
        .exclude(employee=employee)
        .select_related("employee")
    )
    names = [f"{r.employee.short_name} ({r.start_date:%d.%m}–{r.end_date:%d.%m})" for r in overlapping[:8]]
    count = overlapping.count()
    percent = int(count * 100 / staff_total) if staff_total else 0
    return {
        "colleagues": count,
        "total": staff_total,
        "percent": percent,
        "names": names,
        "is_risky": percent >= settings.VACATION_MAX_OVERLAP_PERCENT,
    }


# ---------------------------------------------------------------------------
# Движение заявления
# ---------------------------------------------------------------------------
def _notify(employee: Employee | None, *, title: str, body: str = "", url: str = "", kind: str, actor=None):
    Notification.push(employee, title=title, body=body, url=url, kind=kind, actor=actor)
    if settings.PORTAL_NOTIFY_BY_EMAIL and employee and employee.email:
        try:
            from django.core.mail import send_mail

            send_mail(
                subject=title,
                message=f"{body}\n\n{settings.PORTAL_SITE_NAME}",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[employee.email],
                fail_silently=True,
            )
        except Exception:  # noqa: BLE001
            logger.warning("Не удалось отправить письмо %s", employee.email, exc_info=True)


def _step_url(request_obj: VacationRequest) -> str:
    from django.urls import reverse

    return reverse("vacations:request_detail", args=[request_obj.pk])


@transaction.atomic
def submit_request(request_obj: VacationRequest, actor: Employee | None = None) -> VacationRequest:
    """Отправляет заявление по маршруту согласования."""
    if request_obj.status not in {VacationRequest.Status.DRAFT, VacationRequest.Status.REJECTED}:
        raise VacationError("Заявление уже отправлено или закрыто.")
    employee = request_obj.employee
    context = validate_request(
        employee,
        request_obj.period,
        request_obj.vacation_type,
        request_obj.start_date,
        request_obj.end_date,
        exclude_request_id=request_obj.pk,
        actor=actor,
    )
    request_obj.days_count = context["days"]

    if not request_obj.vacation_type.requires_approval:
        request_obj.status = VacationRequest.Status.APPROVED
        request_obj.submitted_at = timezone.now()
        request_obj.decided_at = timezone.now()
        request_obj.decision_comment = "Согласование не требуется для этого вида отпуска."
        request_obj.save()
        AuditEvent.record(
            "vacation.auto_approved", actor=actor or employee, target=request_obj,
            payload={"days": request_obj.days_count},
        )
        return request_obj

    route = resolve_route(employee, request_obj.vacation_type)
    if route.problems:
        raise VacationError(
            "Маршрут согласования не настроен: " + "; ".join(route.problems) + ". Обратитесь в отдел кадров."
        )
    request_obj.chain_template = route.template
    steps = route.usable_steps

    request_obj.steps.all().delete()
    if not steps:
        # все шаги маршрута оказались неприменимы — фиксируем и согласуем автоматически
        request_obj.status = VacationRequest.Status.APPROVED
        request_obj.submitted_at = timezone.now()
        request_obj.decided_at = timezone.now()
        request_obj.decision_comment = "Маршрут не содержит применимых шагов — отпуск согласован автоматически."
        request_obj.save()
        AuditEvent.record(
            "vacation.auto_approved_empty_route",
            actor=actor or employee,
            target=request_obj,
            payload={"template": route.template.name if route.template else ""},
        )
        _notify(
            employee,
            title=f"Отпуск {request_obj.start_date:%d.%m.%Y}–{request_obj.end_date:%d.%m.%Y} согласован",
            body="Маршрут согласования не потребовался.",
            url=_step_url(request_obj),
            kind=Notification.Kind.VACATION,
        )
        return request_obj

    for resolved in steps:
        VacationRequestStep.objects.create(
            request=request_obj,
            order=resolved.order,
            title=resolved.title,
            approver_kind=resolved.approver_kind,
            approver=resolved.approver,
            approver_label=resolved.approver.full_name if resolved.approver else "—",
        )

    request_obj.status = VacationRequest.Status.PENDING
    request_obj.submitted_at = timezone.now()
    request_obj.decided_at = None
    request_obj.decision_comment = ""
    request_obj.save()

    first_step = request_obj.steps.order_by("order").first()
    if first_step and first_step.approver_id:
        first_step.notified_at = timezone.now()
        first_step.save(update_fields=["notified_at"])
        _notify(
            first_step.approver,
            title=f"На согласование: отпуск {employee.short_name}",
            body=(
                f"{employee.full_name} — {employee.job_title or 'сотрудник'}; "
                f"{request_obj.start_date:%d.%m.%Y}–{request_obj.end_date:%d.%m.%Y} "
                f"({request_obj.days_count} дн.)"
            ),
            url=_step_url(request_obj),
            kind=Notification.Kind.APPROVAL,
            actor=employee,
        )

    AuditEvent.record(
        "vacation.submitted",
        actor=actor or employee,
        target=request_obj,
        payload={
            "route": route.template.name if route.template else "",
            "steps": [s.approver.full_name if s.approver else None for s in steps],
            "days": request_obj.days_count,
        },
    )
    return request_obj


def _advance(request_obj: VacationRequest, actor: Employee | None = None) -> VacationRequest:
    """Передаёт заявление на следующий шаг или завершает согласование."""
    next_step = (
        request_obj.steps.filter(status=VacationRequestStep.Status.PENDING)
        .order_by("order")
        .first()
    )
    if next_step is None:
        request_obj.status = VacationRequest.Status.APPROVED
        request_obj.decided_at = timezone.now()
        request_obj.save(update_fields=["status", "decided_at", "updated_at"])
        AuditEvent.record(
            "vacation.approved",
            target=request_obj,
            actor=actor or request_obj.employee,
            payload={"days": request_obj.days_count},
        )
        _notify(
            request_obj.employee,
            title=f"Отпуск согласован: {request_obj.start_date:%d.%m.%Y}–{request_obj.end_date:%d.%m.%Y}",
            body="Заявление прошло все шаги согласования и включено в график.",
            url=_step_url(request_obj),
            kind=Notification.Kind.VACATION,
        )
        return request_obj

    if next_step.approver_id:
        next_step.notified_at = timezone.now()
        next_step.save(update_fields=["notified_at"])
        _notify(
            next_step.approver,
            title=f"На согласование: отпуск {request_obj.employee.short_name}",
            body=(
                f"{request_obj.employee.full_name}; "
                f"{request_obj.start_date:%d.%m.%Y}–{request_obj.end_date:%d.%m.%Y}"
            ),
            url=_step_url(request_obj),
            kind=Notification.Kind.APPROVAL,
        )
    return request_obj


@transaction.atomic
def approve_step(step: VacationRequestStep, actor: Employee, comment: str = "") -> VacationRequest:
    request_obj = step.request
    lock = VacationRequest.objects.select_for_update().get(pk=request_obj.pk)
    step = VacationRequestStep.objects.select_for_update().get(pk=step.pk)
    if lock.status != VacationRequest.Status.PENDING:
        raise VacationError("Заявление уже закрыто — согласовать его нельзя.")
    if step.status != VacationRequestStep.Status.PENDING:
        raise VacationError("Этот шаг уже обработан.")
    current = lock.current_step
    if current is None or current.pk != step.pk:
        raise VacationError("Сейчас согласует другой участник маршрута. Обновите страницу.")
    if actor.pk not in {step.approver_id, None} and not (actor.is_hr or actor.is_portal_admin):
        raise VacationError("Вы не являетесь согласующим на этом шаге.")

    step.status = VacationRequestStep.Status.APPROVED
    step.acted_by = actor
    step.comment = comment
    step.decided_at = timezone.now()
    step.save(update_fields=["status", "acted_by", "comment", "decided_at"])

    if comment:
        lock.decision_comment = comment
        lock.save(update_fields=["decision_comment", "updated_at"])

    AuditEvent.record(
        "vacation.step_approved",
        actor=actor,
        target=lock,
        payload={"step": step.title, "comment": comment[:500]},
    )
    return _advance(lock, actor)


@transaction.atomic
def reject_step(step: VacationRequestStep, actor: Employee, comment: str) -> VacationRequest:
    if not comment.strip():
        raise VacationError("При отказе необходимо указать причину.")
    lock = VacationRequest.objects.select_for_update().get(pk=step.request_id)
    step = VacationRequestStep.objects.select_for_update().get(pk=step.pk)
    if lock.status != VacationRequest.Status.PENDING:
        raise VacationError("Заявление уже закрыто.")
    if step.status != VacationRequestStep.Status.PENDING:
        raise VacationError("Этот шаг уже обработан.")
    current = lock.current_step
    if current is None or current.pk != step.pk:
        raise VacationError("Сейчас согласует другой участник маршрута.")
    if actor.pk != step.approver_id and not (actor.is_hr or actor.is_portal_admin):
        raise VacationError("Вы не являетесь согласующим на этом шаге.")

    step.status = VacationRequestStep.Status.REJECTED
    step.acted_by = actor
    step.comment = comment
    step.decided_at = timezone.now()
    step.save(update_fields=["status", "acted_by", "comment", "decided_at"])

    lock.steps.filter(status=VacationRequestStep.Status.PENDING).exclude(pk=step.pk).update(
        status=VacationRequestStep.Status.CANCELLED
    )
    lock.status = VacationRequest.Status.REJECTED
    lock.decision_comment = comment
    lock.decided_at = timezone.now()
    lock.save(update_fields=["status", "decision_comment", "decided_at", "updated_at"])

    AuditEvent.record(
        "vacation.rejected", actor=actor, target=lock, payload={"step": step.title, "reason": comment[:500]}
    )
    _notify(
        lock.employee,
        title=f"Отпуск отклонён: {lock.start_date:%d.%m.%Y}–{lock.end_date:%d.%m.%Y}",
        body=f"Причина: {comment}",
        url=_step_url(lock),
        kind=Notification.Kind.VACATION,
        actor=actor,
    )
    return lock


@transaction.atomic
def cancel_request(request_obj: VacationRequest, actor: Employee, reason: str = "") -> VacationRequest:
    lock = VacationRequest.objects.select_for_update().get(pk=request_obj.pk)
    if lock.status in {VacationRequest.Status.CANCELLED, VacationRequest.Status.REJECTED}:
        raise VacationError("Заявление уже отменено.")
    if lock.status == VacationRequest.Status.APPROVED and not (actor.is_hr or actor.is_portal_admin):
        raise VacationError("Согласованный отпуск отменяет отдел кадров.")
    if actor.pk != lock.employee_id and not (actor.is_hr or actor.is_portal_admin):
        raise VacationError("Отозвать заявление может только его автор.")

    lock.steps.filter(status=VacationRequestStep.Status.PENDING).update(
        status=VacationRequestStep.Status.CANCELLED
    )
    lock.status = VacationRequest.Status.CANCELLED
    lock.cancelled_at = timezone.now()
    lock.cancelled_by = actor
    lock.decision_comment = reason or lock.decision_comment
    lock.save(update_fields=["status", "cancelled_at", "cancelled_by", "decision_comment", "updated_at"])

    if lock.chain_template_id:
        for step in lock.steps.exclude(approver=None).select_related("approver"):
            if step.approver_id and step.approver_id != actor.pk:
                _notify(
                    step.approver,
                    title=f"Отозвано: отпуск {lock.employee.short_name}",
                    body=reason or "Заявление отозвано автором.",
                    url=_step_url(lock),
                    kind=Notification.Kind.VACATION,
                    actor=actor,
                )

    AuditEvent.record(
        "vacation.cancelled", actor=actor, target=lock, payload={"reason": reason[:500]}
    )
    return lock


# ---------------------------------------------------------------------------
# Выборки для интерфейса
# ---------------------------------------------------------------------------
def pending_steps_for(employee: Employee):
    """Шаги, по которым сотрудник должен принять решение прямо сейчас."""
    from django.db.models import OuterRef, Subquery

    first_pending = (
        VacationRequestStep.objects.filter(
            request=OuterRef("request"), status=VacationRequestStep.Status.PENDING
        )
        .order_by("order", "id")
        .values("pk")[:1]
    )
    return (
        VacationRequestStep.objects.filter(
            approver=employee,
            status=VacationRequestStep.Status.PENDING,
            request__status=VacationRequest.Status.PENDING,
        )
        .filter(pk=Subquery(first_pending))
        .exclude(request__employee=employee)
        .select_related("request", "request__employee", "request__vacation_type", "request__period")
        .order_by("request__start_date")
    )


def schedule_entries(year: int, department=None):
    """Согласованные отпуска года — основа графика."""
    qs = VacationRequest.objects.filter(
        period__year=year,
        status=VacationRequest.Status.APPROVED,
    ).select_related("employee", "employee__department", "vacation_type")
    if department is not None:
        qs = qs.filter(employee__department=department)
    return qs.order_by("employee__last_name", "start_date")


def open_periods():
    """Периоды, в которые сотрудник может подать заявление (от свежих к старым).

    Возвращает QuerySet: он используется и в формах, и в фильтрах интерфейса.
    Условие повторяет ``SchedulePeriod.accepts_requests`` на стороне SQL.
    """
    today = timezone.localdate()
    return (
        SchedulePeriod.objects.filter(
            status__in=[SchedulePeriod.Status.COLLECTING, SchedulePeriod.Status.REVIEW]
        )
        .filter(Q(submission_deadline__isnull=True) | Q(submission_deadline__gte=today))
        .order_by("-year")
    )


def current_period(create: bool = False) -> SchedulePeriod | None:
    """Активный период графика.

    По умолчанию выбирается самый свежий открытый период: в ноябре это график
    на следующий год, в остальное время — текущий. Сотрудник может переключить
    год прямо в форме заявления.
    """
    today = timezone.localdate()
    available = list(open_periods())
    if available:
        return available[0]
    candidates = SchedulePeriod.objects.exclude(status=SchedulePeriod.Status.CLOSED).order_by("-year")
    upcoming = [period for period in candidates if period.year >= today.year]
    if upcoming:
        return upcoming[0]
    if create:
        return SchedulePeriod.objects.create(year=today.year)
    return SchedulePeriod.objects.order_by("-year").first()


def period_for_year(year: int, create: bool = False) -> SchedulePeriod | None:
    period = SchedulePeriod.objects.filter(year=year).first()
    if period is None and create:
        period = SchedulePeriod.objects.create(year=year)
    return period


def staff_on_vacation_today(department_ids: list[int] | None = None) -> list[VacationRequest]:
    today = timezone.localdate()
    qs = VacationRequest.objects.filter(
        status=VacationRequest.Status.APPROVED, start_date__lte=today, end_date__gte=today
    ).select_related("employee", "vacation_type")
    if department_ids:
        qs = qs.filter(employee__department_id__in=department_ids)
    return list(qs)


def department_load(year: int) -> list[dict]:
    """Загруженность подразделений отпусками по месяцам — для графика."""
    rows = (
        VacationRequest.objects.filter(
            period__year=year,
            status=VacationRequest.Status.APPROVED,
            employee__is_active=True,
        )
        .values("employee__department__id", "employee__department__name")
        .annotate(days=Sum("days_count"), people=Sum(1))
        .order_by("employee__department__name")
    )
    result = []
    for row in rows:
        department_id = row["employee__department__id"]
        total_staff = (
            Employee.objects.filter(department_id=department_id, is_active=True).count()
            if department_id
            else 0
        )
        result.append(
            {
                "department_id": department_id,
                "department": row["employee__department__name"] or "Без подразделения",
                "people": row["people"],
                "days": row["days"] or 0,
                "staff": total_staff,
                "coverage": int(100 * (total_staff - (row["people"] or 0)) / total_staff) if total_staff else 0,
            }
        )
    return result


def request_queryset_for(viewer: Employee):
    """Заявления, которые сотрудник вправе видеть."""
    qs = VacationRequest.objects.select_related(
        "employee", "employee__department", "vacation_type", "period"
    )
    if viewer.is_hr or viewer.is_portal_admin:
        return qs
    subordinate_ids = [e.pk for e in viewer.subordinates_tree(depth=5)]
    return qs.filter(
        Q(employee=viewer)
        | Q(steps__approver=viewer)
        | Q(employee_id__in=subordinate_ids)
    ).distinct()
