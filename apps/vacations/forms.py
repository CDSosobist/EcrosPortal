"""Формы графика отпусков."""
from __future__ import annotations

from django import forms
from django.forms import inlineformset_factory

from apps.accounts.models import Employee

from .models import (
    ApprovalChainStep,
    ApprovalChainTemplate,
    Holiday,
    SchedulePeriod,
    VacationRequest,
    VacationType,
)

DATE_INPUT_FORMATS = ["%Y-%m-%d", "%d.%m.%Y"]
DATE_WIDGET = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class VacationRequestForm(forms.ModelForm):
    """Заявление сотрудника на отпуск."""

    employee = forms.ModelChoiceField(
        queryset=Employee.objects.none(),
        required=False,
        label="Сотрудник",
        help_text="Заполняется сотрудником отдела кадров при оформлении за коллегу.",
        widget=forms.Select(attrs={"class": "input"}),
    )

    period = forms.ModelChoiceField(
        queryset=SchedulePeriod.objects.none(),
        required=True,
        label="График на год",
        empty_label=None,
        widget=forms.Select(attrs={"class": "input"}),
    )

    class Meta:
        model = VacationRequest
        fields = ("vacation_type", "start_date", "end_date", "employee_comment")
        widgets = {
            "vacation_type": forms.Select(attrs={"class": "input"}),
            "start_date": DATE_WIDGET,
            "end_date": DATE_WIDGET,
            "employee_comment": forms.Textarea(
                attrs={"rows": 3, "placeholder": "Пожелания, причина, ссылка на документ"}
            ),
        }

    def __init__(
        self,
        *args,
        employee: Employee | None = None,
        allow_other_employee: bool = False,
        periods=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.employee = employee
        self.fields["vacation_type"].queryset = VacationType.objects.filter(is_active=True)
        self.fields["period"].queryset = periods or SchedulePeriod.objects.filter(
            status__in=[SchedulePeriod.Status.COLLECTING, SchedulePeriod.Status.REVIEW]
        ).order_by("-year")
        if self.instance.pk and self.instance.period_id:
            self.fields["period"].initial = self.instance.period_id
        self.fields["vacation_type"].empty_label = None
        for name in ("start_date", "end_date"):
            self.fields[name].input_formats = DATE_INPUT_FORMATS
            self.fields[name].widget.attrs["class"] = "input"
        # маркеры для живого подсчёта дней в static/js/app.js
        self.fields["start_date"].widget.attrs["data-vacation-start"] = ""
        self.fields["end_date"].widget.attrs["data-vacation-end"] = ""
        self.fields["employee_comment"].widget.attrs["class"] = "input"
        if allow_other_employee:
            self.fields["employee"].queryset = Employee.objects.active().with_org().order_by(
                "last_name", "first_name"
            )
            self.fields["employee"].initial = employee
        else:
            del self.fields["employee"]

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_date")
        end = cleaned.get("end_date")
        if start and end and end < start:
            raise forms.ValidationError("Дата окончания не может быть раньше даты начала.")
        return cleaned


class CancelRequestForm(forms.Form):
    reason = forms.CharField(
        label="Причина отзыва",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "class": "input"}),
    )


class RejectForm(forms.Form):
    comment = forms.CharField(
        label="Причина отказа",
        required=True,
        widget=forms.Textarea(
            attrs={"rows": 3, "class": "input", "placeholder": "Что нужно исправить или почему отказ"}
        ),
    )


class ApproveForm(forms.Form):
    comment = forms.CharField(
        label="Комментарий (необязательно)",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "class": "input"}),
    )


class SchedulePeriodForm(forms.ModelForm):
    class Meta:
        model = SchedulePeriod
        fields = ("year", "status", "submission_deadline", "note")
        widgets = {
            "year": forms.NumberInput(attrs={"class": "input", "min": 2020, "max": 2100}),
            "status": forms.Select(attrs={"class": "input"}),
            "submission_deadline": forms.DateInput(attrs={"type": "date", "class": "input"}),
            "note": forms.Textarea(attrs={"rows": 2, "class": "input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["submission_deadline"].input_formats = DATE_INPUT_FORMATS


class VacationTypeForm(forms.ModelForm):
    class Meta:
        model = VacationType
        fields = (
            "code",
            "name",
            "default_days",
            "consumes_balance",
            "requires_approval",
            "color",
            "is_active",
            "sort_order",
        )
        widgets = {
            "code": forms.TextInput(attrs={"class": "input"}),
            "name": forms.TextInput(attrs={"class": "input"}),
            "default_days": forms.NumberInput(attrs={"class": "input", "min": 0, "max": 365}),
            "color": forms.TextInput(attrs={"class": "input", "type": "color"}),
            "sort_order": forms.NumberInput(attrs={"class": "input"}),
        }


class HolidayForm(forms.ModelForm):
    class Meta:
        model = Holiday
        fields = ("date", "name", "is_working")
        widgets = {
            "date": forms.DateInput(attrs={"type": "date", "class": "input"}),
            "name": forms.TextInput(attrs={"class": "input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].input_formats = DATE_INPUT_FORMATS


class ApprovalChainTemplateForm(forms.ModelForm):
    class Meta:
        model = ApprovalChainTemplate
        fields = (
            "code",
            "name",
            "description",
            "department",
            "applies_to_roles",
            "vacation_types",
            "is_default",
            "is_active",
            "priority",
        )
        widgets = {
            "code": forms.TextInput(attrs={"class": "input"}),
            "name": forms.TextInput(attrs={"class": "input"}),
            "description": forms.Textarea(attrs={"rows": 2, "class": "input"}),
            "department": forms.Select(attrs={"class": "input"}),
            "applies_to_roles": forms.SelectMultiple(attrs={"class": "input", "size": 6}),
            "vacation_types": forms.SelectMultiple(attrs={"class": "input", "size": 6}),
            "priority": forms.NumberInput(attrs={"class": "input", "min": 0}),
        }


ApprovalChainStepFormSet = inlineformset_factory(
    ApprovalChainTemplate,
    ApprovalChainStep,
    fields=("order", "title", "approver_kind", "role", "user", "skip_if_missing"),
    extra=4,
    can_delete=True,
    widgets={
        "order": forms.NumberInput(attrs={"class": "input", "min": 1, "style": "width:70px"}),
        "title": forms.TextInput(attrs={"class": "input", "placeholder": "Например: Согласование руководителя"}),
        "approver_kind": forms.Select(attrs={"class": "input"}),
        "role": forms.Select(attrs={"class": "input"}),
        "user": forms.Select(attrs={"class": "input"}),
    },
)
