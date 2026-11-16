"""Интерфейс графика отпусков: заявления, согласование, график, настройки."""
from __future__ import annotations

import calendar
import csv
from collections import defaultdict
from datetime import date, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import AuditEvent, Department, Employee, Role
from apps.accounts.permissions import (
    can_manage_approval_chains,
    can_manage_vacation_balance,
)
from . import services
from .forms import (
    ApprovalChainStepFormSet,
    ApprovalChainTemplateForm,
    ApproveForm,
    CancelRequestForm,
    HolidayForm,
    RejectForm,
    SchedulePeriodForm,
    VacationRequestForm,
    VacationTypeForm,
)
from .models import (
    ApprovalChainTemplate,
    Holiday,
    SchedulePeriod,
    VacationBalance,
    VacationRequest,
    VacationRequestStep,
    VacationType,
)

MONTH_NAMES = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]
MONTH_SHORT = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------
def _resolve_year(request, default: int | None = None) -> int:
    raw = request.GET.get("year") or request.POST.get("year")
    if raw and str(raw).isdigit():
        return int(raw)
    if default:
        return default
    period = services.current_period()
    if period:
        return period.year
    return timezone.localdate().year


def available_years() -> list[int]:
    years = set(SchedulePeriod.objects.values_list("year", flat=True))
    years.update(VacationRequest.objects.values_list("period__year", flat=True))
    this_year = timezone.localdate().year
    years.update({this_year, this_year + 1})
    return sorted(years, reverse=True)


def can_view_request(viewer: Employee, request_obj: VacationRequest) -> bool:
    if viewer.is_hr or viewer.is_portal_admin:
        return True
    if request_obj.employee_id == viewer.pk:
        return True
    if request_obj.steps.filter(approver=viewer).exists():
        return True
    manager_ids = {e.pk for e in request_obj.employee.manager_chain(max_depth=8)}
    return viewer.pk in manager_ids


def _scope_departments(viewer: Employee):
    if viewer.is_hr or viewer.is_portal_admin:
        return Department.objects.filter(is_active=True)
    if viewer.department_id and viewer.is_manager:
        ids = viewer.department.descendant_ids(include_self=True)
        return Department.objects.filter(pk__in=ids)
    return Department.objects.none()


# ---------------------------------------------------------------------------
# Мои отпуска
# ---------------------------------------------------------------------------
@login_required
def index(request):
    viewer = request.user
    period = services.current_period()
    year = _resolve_year(request, period.year if period else None)
    summary = services.balance_summary(viewer, year)
    my_requests = (
        VacationRequest.objects.filter(employee=viewer)
        .select_related("vacation_type", "period", "chain_template")
        .order_by("-start_date")[:40]
    )
    approvals = list(services.pending_steps_for(viewer)[:5])
    today = timezone.localdate()
    upcoming = (
        VacationRequest.objects.filter(
            employee=viewer,
            status=VacationRequest.Status.APPROVED,
            end_date__gte=today,
        )
        .select_related("vacation_type")
        .order_by("start_date")
        .first()
    )
    colleagues_on_vacation = (
        VacationRequest.objects.filter(
            status=VacationRequest.Status.APPROVED,
            start_date__lte=today + timedelta(days=30),
            end_date__gte=today,
            employee__department=viewer.department,
        )
        .exclude(employee=viewer)
        .select_related("employee", "vacation_type")
        .order_by("start_date")[:8]
        if viewer.department_id
        else []
    )
    return render(
        request,
        "vacations/index.html",
        {
            "period": period,
            "year": year,
            "years": available_years(),
            "summary": summary,
            "my_requests": my_requests,
            "approvals": approvals,
            "upcoming": upcoming,
            "today": today,
            "colleagues_on_vacation": colleagues_on_vacation,
            "types": VacationType.objects.filter(is_active=True),
            "max_days": settings.VACATION_MAX_DAYS_PER_REQUEST,
            "min_notice_days": settings.VACATION_MIN_NOTICE_DAYS,
        },
    )


def _request_form_context(viewer: Employee, target: Employee, period: SchedulePeriod | None):
    """Данные для формы: маршрут по умолчанию и подсказки по дням."""
    preview = None
    if period is not None:
        first_type = VacationType.objects.filter(is_active=True, requires_approval=True).first()
        if first_type is not None:
            preview = services.resolve_route(target, first_type)
    return {
        "target": target,
        "period": period,
        "route_preview": preview,
        "summary": services.balance_summary(target, period.year if period else timezone.localdate().year),
        "today": timezone.localdate(),
        "max_days": settings.VACATION_MAX_DAYS_PER_REQUEST,
        "min_notice_days": settings.VACATION_MIN_NOTICE_DAYS,
        "holidays_json": _holidays_json(period.year if period else timezone.localdate().year),
    }


def _holidays_json(year: int) -> str:
    """Календарь праздников для живого подсчёта дней в браузере.

    Отдаём текущий год и два следующих: сотрудник может выбрать любой из
    открытых периодов, и счётчик должен оставаться корректным.
    """
    import json

    days = list(
        Holiday.objects.filter(
            date__year__gte=year, date__year__lte=year + 2, is_working=False
        ).values_list("date", flat=True)
    )
    return json.dumps([d.isoformat() for d in days])


@login_required
def request_create(request):
    viewer = request.user
    can_act_for_others = viewer.is_hr or viewer.is_portal_admin
    target = viewer
    if can_act_for_others and request.GET.get("employee"):
        target = get_object_or_404(Employee, pk=request.GET["employee"])
    period = services.current_period(create=True)

    if period is None:  # pragma: no cover - период создаётся выше
        messages.error(request, "Не настроен период графика отпусков. Обратитесь в отдел кадров.")
        return redirect("vacations:index")

    if request.method == "POST":
        form = VacationRequestForm(
            request.POST,
            employee=target,
            allow_other_employee=can_act_for_others,
            periods=services.open_periods() or None,
        )
        if can_act_for_others and form.data.get("employee"):
            target = Employee.objects.filter(pk=form.data.get("employee")).first() or target
        if form.is_valid():
            period = form.cleaned_data["period"]
            vacation_type = form.cleaned_data["vacation_type"]
            start = form.cleaned_data["start_date"]
            end = form.cleaned_data["end_date"]
            try:
                services.validate_request(
                    target, period, vacation_type, start, end, actor=viewer
                )
            except services.VacationError as exc:
                form.add_error(None, str(exc))
            else:
                with transaction.atomic():
                    request_obj = VacationRequest.objects.create(
                        employee=target,
                        period=period,
                        vacation_type=vacation_type,
                        start_date=start,
                        end_date=end,
                        days_count=services.count_vacation_days(start, end),
                        employee_comment=form.cleaned_data.get("employee_comment", ""),
                    )
                    try:
                        services.submit_request(request_obj, actor=viewer)
                    except services.VacationError as exc:
                        form.add_error(None, str(exc))
                    else:
                        messages.success(
                            request,
                            f"Заявление {request_obj.number} отправлено на согласование."
                            if request_obj.status == VacationRequest.Status.PENDING
                            else f"Заявление {request_obj.number} согласовано.",
                        )
                        return redirect("vacations:request_detail", pk=request_obj.pk)
        messages.error(request, "Исправьте ошибки в форме.")
    else:
        form = VacationRequestForm(
            employee=target,
            allow_other_employee=can_act_for_others,
            periods=services.open_periods() or None,
            initial={"period": period.pk if period else None},
        )

    context = {"form": form, **_request_form_context(viewer, target, period)}
    return render(request, "vacations/request_form.html", context)


@login_required
def request_edit(request, pk: int):
    viewer = request.user
    request_obj = get_object_or_404(
        VacationRequest.objects.select_related("employee", "period", "vacation_type"), pk=pk
    )
    if not can_view_request(viewer, request_obj):
        raise PermissionDenied("Нет доступа к этому заявлению")
    is_owner = request_obj.employee_id == viewer.pk
    if not (is_owner or viewer.is_hr or viewer.is_portal_admin):
        raise PermissionDenied("Редактировать заявление может только автор")
    if not request_obj.is_editable:
        messages.error(request, "Изменять можно только черновик или отклонённое заявление.")
        return redirect("vacations:request_detail", pk=request_obj.pk)

    period = request_obj.period
    if request.method == "POST":
        form = VacationRequestForm(request.POST, instance=request_obj, periods=[period])
        if form.is_valid():
            start = form.cleaned_data["start_date"]
            end = form.cleaned_data["end_date"]
            vacation_type = form.cleaned_data["vacation_type"]
            try:
                services.validate_request(
                    request_obj.employee,
                    period,
                    vacation_type,
                    start,
                    end,
                    exclude_request_id=request_obj.pk,
                    actor=viewer,
                )
            except services.VacationError as exc:
                form.add_error(None, str(exc))
            else:
                with transaction.atomic():
                    form.save()
                    try:
                        services.submit_request(request_obj, actor=viewer)
                    except services.VacationError as exc:
                        form.add_error(None, str(exc))
                    else:
                        messages.success(request, "Заявление обновлено и отправлено на согласование.")
                        return redirect("vacations:request_detail", pk=request_obj.pk)
        messages.error(request, "Исправьте ошибки в форме.")
    else:
        form = VacationRequestForm(instance=request_obj, periods=[period])

    context = {"form": form, "request_obj": request_obj, **_request_form_context(viewer, request_obj.employee, period)}
    return render(request, "vacations/request_form.html", context)


@login_required
def request_detail(request, pk: int):
    viewer = request.user
    request_obj = get_object_or_404(
        VacationRequest.objects.select_related(
            "employee", "employee__department", "employee__manager", "period", "vacation_type", "chain_template"
        ),
        pk=pk,
    )
    if not can_view_request(viewer, request_obj):
        raise PermissionDenied("Нет доступа к этому заявлению")

    steps = list(request_obj.steps_ordered())
    current = request_obj.current_step
    can_decide = (
        current is not None
        and current.approver_id is not None
        and (current.approver_id == viewer.pk or viewer.is_hr or viewer.is_portal_admin)
    )
    is_owner = request_obj.employee_id == viewer.pk
    audit = (
        AuditEvent.objects.filter(
            Q(target_id=str(request_obj.pk), target_type="VacationRequest")
        )
        .select_related("actor")
        .order_by("created_at")[:40]
    )
    return render(
        request,
        "vacations/request_detail.html",
        {
            "request_obj": request_obj,
            "steps": steps,
            "current": current,
            "can_decide": can_decide,
            "is_owner": is_owner,
            "approve_form": ApproveForm(),
            "reject_form": RejectForm(),
            "cancel_form": CancelRequestForm(),
            "overlaps": request_obj.overlaps_other_requests[:6],
            "audit": audit,
            "summary": services.balance_summary(request_obj.employee, request_obj.period.year),
        },
    )


@login_required
def request_list(request):
    viewer = request.user
    qs = services.request_queryset_for(viewer)
    status = request.GET.get("status", "")
    year = request.GET.get("year", "")
    query = request.GET.get("q", "").strip()
    if status:
        qs = qs.filter(status=status)
    if year.isdigit():
        qs = qs.filter(period__year=int(year))
    if query:
        qs = qs.filter(
            Q(employee__last_name__icontains=query)
            | Q(employee__first_name__icontains=query)
            | Q(employee__username__icontains=query)
        )
    qs = qs.order_by("-created_at")
    return render(
        request,
        "vacations/request_list.html",
        {
            "requests": qs[:300],
            "total": qs.count(),
            "status": status,
            "year": year,
            "query": query,
            "years": available_years(),
            "statuses": VacationRequest.Status.choices,
        },
    )


@login_required
@require_POST
def request_submit(request, pk: int):
    request_obj = get_object_or_404(VacationRequest, pk=pk)
    if request_obj.employee_id != request.user.pk and not (
        request.user.is_hr or request.user.is_portal_admin
    ):
        raise PermissionDenied("Нет доступа")
    try:
        services.submit_request(request_obj, actor=request.user)
        messages.success(request, "Заявление отправлено на согласование.")
    except services.VacationError as exc:
        messages.error(request, str(exc))
    return redirect("vacations:request_detail", pk=request_obj.pk)


@login_required
@require_POST
def request_cancel(request, pk: int):
    request_obj = get_object_or_404(VacationRequest, pk=pk)
    form = CancelRequestForm(request.POST)
    reason = form.cleaned_data["reason"] if form.is_valid() else request.POST.get("reason", "")
    try:
        services.cancel_request(request_obj, request.user, reason)
        messages.success(request, "Заявление отозвано.")
    except services.VacationError as exc:
        messages.error(request, str(exc))
    return redirect("vacations:request_detail", pk=request_obj.pk)


@login_required
@require_POST
def step_approve(request, pk: int):
    step = get_object_or_404(
        VacationRequestStep.objects.select_related("request", "request__employee"), pk=pk
    )
    form = ApproveForm(request.POST)
    comment = form.cleaned_data["comment"] if form.is_valid() else ""
    try:
        request_obj = services.approve_step(step, request.user, comment)
        if request_obj.status == VacationRequest.Status.APPROVED:
            messages.success(request, "Заявление согласовано. Отпуск включён в график.")
        else:
            messages.success(request, "Шаг согласован, заявление передано следующему согласующему.")
    except services.VacationError as exc:
        messages.error(request, str(exc))
    return redirect("vacations:request_detail", pk=step.request_id)


@login_required
@require_POST
def step_reject(request, pk: int):
    step = get_object_or_404(VacationRequestStep, pk=pk)
    form = RejectForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Укажите причину отказа.")
        return redirect("vacations:request_detail", pk=step.request_id)
    try:
        services.reject_step(step, request.user, form.cleaned_data["comment"])
        messages.success(request, "Заявление отклонено, автор уведомлён.")
    except services.VacationError as exc:
        messages.error(request, str(exc))
    return redirect("vacations:request_detail", pk=step.request_id)


# ---------------------------------------------------------------------------
# Согласование
# ---------------------------------------------------------------------------
@login_required
def approvals(request):
    viewer = request.user
    steps = list(services.pending_steps_for(viewer))
    decided = (
        VacationRequestStep.objects.filter(acted_by=viewer)
        .select_related("request", "request__employee", "request__vacation_type")
        .order_by("-decided_at")[:25]
    )
    scope = _scope_departments(viewer)
    team_pending = (
        VacationRequest.objects.filter(
            status=VacationRequest.Status.PENDING, employee__department__in=scope
        )
        .select_related("employee", "vacation_type")
        .order_by("start_date")[:25]
        if scope.exists()
        else []
    )
    return render(
        request,
        "vacations/approvals.html",
        {
            "steps": steps,
            "decided": decided,
            "team_pending": team_pending,
            "approve_form": ApproveForm(),
            "reject_form": RejectForm(),
        },
    )


# ---------------------------------------------------------------------------
# График
# ---------------------------------------------------------------------------
def _month_segments(request_obj: VacationRequest, year: int) -> dict[int, list[dict]]:
    """Раскладывает отпуск по месяцам для отрисовки графика."""
    result: dict[int, list[dict]] = defaultdict(list)
    start = max(request_obj.start_date, date(year, 1, 1))
    end = min(request_obj.end_date, date(year, 12, 31))
    if end < start:
        return result
    cursor = start
    while cursor <= end:
        last_day = calendar.monthrange(cursor.year, cursor.month)[1]
        month_end = min(end, date(cursor.year, cursor.month, last_day))
        result[cursor.month].append(
            {
                "start_day": cursor.day,
                "end_day": month_end.day,
                "start": cursor,
                "end": month_end,
                "color": request_obj.vacation_type.color,
                "type": request_obj.vacation_type.name,
                "days": (month_end - cursor).days + 1,
                "request": request_obj,
            }
        )
        cursor = month_end + timedelta(days=1)
    return result


@login_required
def schedule(request, year: int | None = None):
    viewer = request.user
    year = year or _resolve_year(request)
    period = services.period_for_year(year, create=False)
    dept_param = request.GET.get("department") or ""
    show_all = request.GET.get("all") == "1"

    department = None
    if dept_param.isdigit():
        department = Department.objects.filter(pk=int(dept_param)).first()

    entries = list(services.schedule_entries(year))
    if department is not None:
        ids = set(department.descendant_ids(include_self=True))
        entries = [e for e in entries if e.employee.department_id in ids]

    by_employee: dict[int, dict] = {}
    for entry in entries:
        item = by_employee.setdefault(
            entry.employee_id,
            {
                "employee": entry.employee,
                "requests": [],
                "segments": defaultdict(list),
                "days": 0,
            },
        )
        item["requests"].append(entry)
        item["days"] += entry.days_count
        for month, segments in _month_segments(entry, year).items():
            item["segments"][month].extend(segments)

    if show_all:
        staff_qs = Employee.objects.filter(is_active=True).select_related("department")
        if department is not None:
            staff_qs = staff_qs.filter(department_id__in=department.descendant_ids(include_self=True))
        for person in staff_qs:
            by_employee.setdefault(
                person.pk,
                {"employee": person, "requests": [], "segments": defaultdict(list), "days": 0},
            )

    groups: dict[str, list[dict]] = defaultdict(list)
    for item in by_employee.values():
        dept_name = item["employee"].department.name if item["employee"].department_id else "Без подразделения"
        segment_grid = []
        for month in range(1, 13):
            segment_grid.append(sorted(item["segments"].get(month, []), key=lambda s: s["start_day"]))
        groups[dept_name].append(
            {
                "employee": item["employee"],
                "months": segment_grid,
                "days": item["days"],
                "requests": item["requests"],
            }
        )

    grouped = []
    for dept_name in sorted(groups):
        rows = sorted(groups[dept_name], key=lambda r: (r["employee"].last_name, r["employee"].first_name))
        grouped.append({"department": dept_name, "rows": rows, "total_days": sum(r["days"] for r in rows)})

    return render(
        request,
        "vacations/schedule.html",
        {
            "year": year,
            "years": available_years(),
            "period": period,
            "grouped": grouped,
            "rows_total": sum(len(g["rows"]) for g in grouped),
            "departments": Department.objects.filter(is_active=True).order_by("sort_order", "name"),
            "department": department,
            "show_all": show_all,
            "month_short": MONTH_SHORT,
            "month_names": MONTH_NAMES,
            "can_manage": can_manage_vacation_balance(viewer),
        },
    )


@login_required
def schedule_export(request, year: int):
    department = None
    dept_param = request.GET.get("department") or ""
    if dept_param.isdigit():
        department = Department.objects.filter(pk=int(dept_param)).first()

    entries = list(services.schedule_entries(year))
    if department is not None:
        ids = set(department.descendant_ids(include_self=True))
        entries = [e for e in entries if e.employee.department_id in ids]

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="grafik-otpuskov-{year}.csv"'
    response.write("\ufeff")
    writer = csv.writer(response, delimiter=";")
    writer.writerow(
        [
            "№",
            "Подразделение",
            "ФИО",
            "Должность",
            "Вид отпуска",
            "Дата начала",
            "Дата окончания",
            "Календарных дней",
            "Статус",
            "Согласовано",
        ]
    )
    total_days = 0
    for index, entry in enumerate(entries, start=1):
        total_days += entry.days_count
        writer.writerow(
            [
                index,
                entry.employee.department.name if entry.employee.department_id else "",
                entry.employee.full_name,
                entry.employee.job_title,
                entry.vacation_type.name,
                entry.start_date.strftime("%d.%m.%Y"),
                entry.end_date.strftime("%d.%m.%Y"),
                entry.days_count,
                entry.get_status_display(),
                entry.decided_at.strftime("%d.%m.%Y") if entry.decided_at else "",
            ]
        )
    writer.writerow([])
    writer.writerow(["", "", "Итого", "", "", "", "", total_days, "", ""])
    return response


@login_required
def calendar_view(request):
    today = timezone.localdate()
    year = int(request.GET.get("year") or today.year)
    month = int(request.GET.get("month") or today.month)
    if not 1 <= month <= 12:
        month = today.month
    first_day = date(year, month, 1)
    last_day = date(year, month, calendar.monthrange(year, month)[1])

    entries = (
        VacationRequest.objects.filter(
            status=VacationRequest.Status.APPROVED,
            start_date__lte=last_day,
            end_date__gte=first_day,
        )
        .select_related("employee", "employee__department", "vacation_type")
        .order_by("employee__last_name")
    )
    days: dict[int, list[VacationRequest]] = defaultdict(list)
    for entry in entries:
        start = max(entry.start_date, first_day)
        end = min(entry.end_date, last_day)
        cursor = start
        while cursor <= end:
            days[cursor.day].append(entry)
            cursor += timedelta(days=1)

    holidays = {
        h.date.day: h for h in Holiday.objects.filter(date__gte=first_day, date__lte=last_day)
    }
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    grid = []
    for week in weeks:
        row = []
        for day in week:
            row.append(
                {
                    "date": day,
                    "in_month": day.month == month,
                    "is_today": day == today,
                    "holiday": holidays.get(day.day) if day.month == month else None,
                    "entries": days.get(day.day, []) if day.month == month else [],
                    "weekend": day.weekday() >= 5,
                }
            )
        grid.append(row)

    prev_month = (first_day - timedelta(days=1)).replace(day=1)
    next_month = (last_day + timedelta(days=1)).replace(day=1)
    return render(
        request,
        "vacations/calendar.html",
        {
            "year": year,
            "month": month,
            "month_name": MONTH_NAMES[month - 1],
            "grid": grid,
            "prev": prev_month,
            "next": next_month,
            "on_vacation_today": services.staff_on_vacation_today(),
            "today": today,
            "vacation_types": VacationType.objects.filter(is_active=True),
        },
    )


@login_required
def team_view(request):
    viewer = request.user
    subordinates = viewer.subordinates_tree(depth=4)
    ids = [e.pk for e in subordinates]
    today = timezone.localdate()
    horizon = today + timedelta(days=120)
    upcoming = (
        VacationRequest.objects.filter(
            employee_id__in=ids,
            status__in=[VacationRequest.Status.APPROVED, VacationRequest.Status.PENDING],
            end_date__gte=today,
            start_date__lte=horizon,
        )
        .select_related("employee", "vacation_type")
        .order_by("start_date")
        if ids
        else []
    )
    current = (
        VacationRequest.objects.filter(
            employee_id__in=ids,
            status=VacationRequest.Status.APPROVED,
            start_date__lte=today,
            end_date__gte=today,
        ).select_related("employee", "vacation_type")
        if ids
        else []
    )
    return render(
        request,
        "vacations/team.html",
        {
            "subordinates": subordinates,
            "upcoming": upcoming,
            "current": current,
            "today": today,
            "horizon": horizon,
        },
    )


# ---------------------------------------------------------------------------
# Настройки (HR / администратор)
# ---------------------------------------------------------------------------
@login_required
def manage_index(request):
    if not can_manage_vacation_balance(request.user):
        raise PermissionDenied("Раздел доступен отделу кадров")
    return render(
        request,
        "vacations/manage/index.html",
        {
            "periods": SchedulePeriod.objects.order_by("-year"),
            "chains": ApprovalChainTemplate.objects.order_by("priority"),
            "types": VacationType.objects.all(),
            "pending_total": VacationRequest.objects.filter(
                status=VacationRequest.Status.PENDING
            ).count(),
        },
    )


@login_required
def manage_periods(request):
    if not can_manage_vacation_balance(request.user):
        raise PermissionDenied("Раздел доступен отделу кадров")

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "create":
            form = SchedulePeriodForm(request.POST)
            if form.is_valid():
                period = form.save()
                created = services.ensure_balances_for_period(period)
                AuditEvent.record(
                    "schedule_period.created",
                    actor=request.user,
                    target=period,
                    payload={"balances_created": created},
                    request=request,
                )
                messages.success(
                    request,
                    f"Период {period.year} создан, права на отпуск оформлены ({created} сотр.).",
                )
            else:
                messages.error(request, "Не удалось создать период: проверьте поля.")
            return redirect("vacations:manage_periods")
        if action in {"approve", "close", "review", "reopen"}:
            period = get_object_or_404(SchedulePeriod, pk=request.POST.get("period_id"))
            mapping = {
                "review": SchedulePeriod.Status.REVIEW,
                "approve": SchedulePeriod.Status.APPROVED,
                "close": SchedulePeriod.Status.CLOSED,
                "reopen": SchedulePeriod.Status.COLLECTING,
            }
            period.status = mapping[action]
            if action == "approve":
                period.published_at = timezone.now()
                period.published_by = request.user
            if action == "reopen":
                period.published_at = None
                period.published_by = None
            period.save()
            AuditEvent.record(
                f"schedule_period.{action}", actor=request.user, target=period, request=request
            )
            messages.success(request, f"Статус периода {period.year} изменён: {period.get_status_display()}.")
            return redirect("vacations:manage_periods")
        if action == "ensure_balances":
            period = get_object_or_404(SchedulePeriod, pk=request.POST.get("period_id"))
            created = services.ensure_balances_for_period(period)
            messages.success(request, f"Права на отпуск оформлены для {created} сотрудников.")
            return redirect("vacations:manage_periods")

    return render(
        request,
        "vacations/manage/periods.html",
        {
            "periods": SchedulePeriod.objects.order_by("-year"),
            "form": SchedulePeriodForm(initial={"year": timezone.localdate().year + 1}),
        },
    )


@login_required
def manage_balances(request):
    if not can_manage_vacation_balance(request.user):
        raise PermissionDenied("Раздел доступен отделу кадров")
    year = _resolve_year(request)
    period = services.period_for_year(year)
    if request.method == "POST":
        updated = 0
        with transaction.atomic():
            for key, value in request.POST.items():
                if not key.startswith("entitled_") or not str(value).strip().isdigit():
                    continue
                balance_id = key.split("_", 1)[1]
                balance = VacationBalance.objects.filter(pk=balance_id).first()
                if balance is None:
                    continue
                entitled = int(value)
                carried_raw = request.POST.get(f"carried_{balance_id}", "0")
                carried = int(carried_raw) if str(carried_raw).strip().isdigit() else 0
                if balance.entitled_days != entitled or balance.carried_over_days != carried:
                    balance.entitled_days = entitled
                    balance.carried_over_days = carried
                    balance.note = request.POST.get(f"note_{balance_id}", "")[:255]
                    balance.save()
                    updated += 1
        if updated:
            AuditEvent.record(
                "vacation.balances_updated", actor=request.user,
                payload={"year": year, "updated": updated}, request=request,
            )
            messages.success(request, f"Обновлено прав на отпуск: {updated}.")
        else:
            messages.info(request, "Изменений нет.")
        return redirect(f"{reverse('vacations:manage_balances')}?year={year}")

    dept_param = request.GET.get("department") or ""
    employees = Employee.objects.filter(is_active=True).select_related("department").order_by(
        "department__name", "last_name"
    )
    if dept_param.isdigit():
        department = Department.objects.filter(pk=int(dept_param)).first()
        if department is not None:
            employees = employees.filter(department_id__in=department.descendant_ids(include_self=True))

    balances = {
        b.employee_id: b
        for b in VacationBalance.objects.filter(year=year, employee__in=employees)
    }
    rows = []
    for employee in employees:
        balance = balances.get(employee.pk)
        used = balance.used_days() if balance else 0
        pending = balance.pending_days() if balance else 0
        rows.append(
            {
                "employee": employee,
                "balance": balance,
                "entitled": balance.entitled_days if balance else 0,
                "carried": balance.carried_over_days if balance else 0,
                "used": used,
                "pending": pending,
                "available": (balance.total_days if balance else 0) - used - pending,
                "note": balance.note if balance else "",
                "id": balance.pk if balance else "",
            }
        )

    return render(
        request,
        "vacations/manage/balances.html",
        {
            "rows": rows,
            "year": year,
            "years": available_years(),
            "period": period,
            "departments": Department.objects.filter(is_active=True).order_by("name"),
            "department_param": dept_param,
        },
    )


@login_required
def manage_chains(request):
    if not can_manage_approval_chains(request.user):
        raise PermissionDenied("Раздел доступен отделу кадров и администраторам")
    return render(
        request,
        "vacations/manage/chains.html",
        {"chains": ApprovalChainTemplate.objects.prefetch_related("steps").order_by("priority", "name")},
    )


@login_required
def chain_edit(request, pk: int | None = None):
    if not can_manage_approval_chains(request.user):
        raise PermissionDenied("Раздел доступен отделу кадров и администраторам")
    instance = get_object_or_404(ApprovalChainTemplate, pk=pk) if pk else None
    if request.method == "POST":
        form = ApprovalChainTemplateForm(request.POST, instance=instance)
        formset = ApprovalChainStepFormSet(request.POST, instance=instance)
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                chain = form.save()
                formset.instance = chain
                formset.save()
            AuditEvent.record(
                "approval_chain.saved", actor=request.user, target=chain,
                payload={"steps": chain.steps.count()}, request=request,
            )
            messages.success(request, f"Маршрут «{chain.name}» сохранён.")
            return redirect("vacations:manage_chains")
        messages.error(request, "Проверьте заполнение маршрута и шагов.")
    else:
        form = ApprovalChainTemplateForm(instance=instance)
        formset = ApprovalChainStepFormSet(instance=instance)

    return render(
        request,
        "vacations/manage/chain_form.html",
        {
            "form": form,
            "formset": formset,
            "chain": instance,
            "roles": Role.objects.all(),
        },
    )


@login_required
@require_POST
def chain_delete(request, pk: int):
    if not can_manage_approval_chains(request.user):
        raise PermissionDenied("Раздел доступен отделу кадров и администраторам")
    chain = get_object_or_404(ApprovalChainTemplate, pk=pk)
    name = chain.name
    chain.is_active = False
    chain.save(update_fields=["is_active"])
    AuditEvent.record("approval_chain.deactivated", actor=request.user, target=chain, request=request)
    messages.success(request, f"Маршрут «{name}» выключен (история согласований сохранена).")
    return redirect("vacations:manage_chains")


@login_required
def manage_types(request):
    if not can_manage_vacation_balance(request.user):
        raise PermissionDenied("Раздел доступен отделу кадров")
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            form = VacationTypeForm(request.POST)
            if form.is_valid():
                vacation_type = form.save()
                AuditEvent.record("vacation_type.created", actor=request.user, target=vacation_type, request=request)
                messages.success(request, f"Вид отпуска «{vacation_type.name}» добавлен.")
            else:
                messages.error(request, "Не удалось добавить вид отпуска.")
        elif action == "update":
            updated = 0
            for key, value in request.POST.items():
                if not key.startswith("name_"):
                    continue
                type_id = key.split("_", 1)[1]
                vacation_type = VacationType.objects.filter(pk=type_id).first()
                if vacation_type is None:
                    continue
                vacation_type.name = value.strip()[:120] or vacation_type.name
                days_raw = request.POST.get(f"default_days_{type_id}", str(vacation_type.default_days))
                vacation_type.default_days = int(days_raw) if str(days_raw).isdigit() else vacation_type.default_days
                vacation_type.is_active = request.POST.get(f"active_{type_id}") == "on"
                vacation_type.consumes_balance = request.POST.get(f"consumes_{type_id}") == "on"
                vacation_type.requires_approval = request.POST.get(f"approval_{type_id}") == "on"
                vacation_type.color = request.POST.get(f"color_{type_id}", vacation_type.color)
                vacation_type.save()
                updated += 1
            messages.success(request, f"Обновлено видов отпуска: {updated}.")
        return redirect("vacations:manage_types")

    return render(
        request,
        "vacations/manage/types.html",
        {
            "types": VacationType.objects.all(),
            "form": VacationTypeForm(initial={"default_days": settings.VACATION_DEFAULT_DAYS}),
        },
    )


@login_required
def manage_holidays(request):
    if not can_manage_vacation_balance(request.user):
        raise PermissionDenied("Раздел доступен отделу кадров")
    year = _resolve_year(request)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            form = HolidayForm(request.POST)
            if form.is_valid():
                form.save()
                messages.success(request, "День добавлен в производственный календарь.")
            else:
                messages.error(request, "Не удалось добавить день: проверьте дату и название.")
        elif action == "delete":
            Holiday.objects.filter(pk=request.POST.get("holiday_id")).delete()
            messages.success(request, "День удалён из календаря.")
        return redirect(f"{reverse('vacations:manage_holidays')}?year={year}")

    holidays = Holiday.objects.filter(date__year=year)
    return render(
        request,
        "vacations/manage/holidays.html",
        {
            "holidays": holidays,
            "year": year,
            "years": available_years(),
            "form": HolidayForm(),
        },
    )
