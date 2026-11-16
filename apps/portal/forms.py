"""Формы портала: заявки, новости, база знаний."""
from __future__ import annotations

from django import forms

from apps.accounts.models import Employee

from .models import KbCategory, KnowledgeArticle, NewsCategory, NewsPost, ServiceTicket, TicketComment


class TicketForm(forms.ModelForm):
    class Meta:
        model = ServiceTicket
        fields = ("category", "priority", "subject", "description")
        widgets = {
            "category": forms.Select(attrs={"class": "input"}),
            "priority": forms.Select(attrs={"class": "input"}),
            "subject": forms.TextInput(attrs={"class": "input", "placeholder": "Коротко о проблеме"}),
            "description": forms.Textarea(
                attrs={
                    "class": "input",
                    "rows": 6,
                    "placeholder": "Опишите ситуацию: что происходит, когда началось, что уже пробовали",
                }
            ),
        }


class TicketCommentForm(forms.ModelForm):
    class Meta:
        model = TicketComment
        fields = ("body", "is_internal")
        widgets = {
            "body": forms.Textarea(
                attrs={"class": "input", "rows": 3, "placeholder": "Добавить комментарий"}
            ),
        }


class TicketStatusForm(forms.Form):
    status = forms.ChoiceField(choices=ServiceTicket.Status.choices, widget=forms.Select(attrs={"class": "input"}))
    assignee = forms.ModelChoiceField(
        queryset=Employee.objects.none(), required=False, label="Исполнитель",
        widget=forms.Select(attrs={"class": "input"}),
    )
    comment = forms.CharField(required=False, widget=forms.Textarea(attrs={"class": "input", "rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assignee"].queryset = Employee.objects.filter(is_active=True).order_by("last_name")


class NewsForm(forms.ModelForm):
    class Meta:
        model = NewsPost
        fields = ("title", "category", "summary", "body", "is_pinned", "is_published")
        widgets = {
            "title": forms.TextInput(attrs={"class": "input"}),
            "category": forms.Select(attrs={"class": "input"}),
            "summary": forms.TextInput(attrs={"class": "input"}),
            "body": forms.Textarea(attrs={"class": "input", "rows": 14}),
        }


class KbArticleForm(forms.ModelForm):
    class Meta:
        model = KnowledgeArticle
        fields = ("title", "category", "summary", "tags", "body", "is_published")
        widgets = {
            "title": forms.TextInput(attrs={"class": "input"}),
            "category": forms.Select(attrs={"class": "input"}),
            "summary": forms.TextInput(attrs={"class": "input"}),
            "tags": forms.TextInput(attrs={"class": "input", "placeholder": "отпуск, заявление, кадры"}),
            "body": forms.Textarea(attrs={"class": "input", "rows": 16}),
        }


class KbCategoryForm(forms.ModelForm):
    class Meta:
        model = KbCategory
        fields = ("name", "code", "description", "sort_order")
        widgets = {
            "name": forms.TextInput(attrs={"class": "input"}),
            "code": forms.TextInput(attrs={"class": "input"}),
            "description": forms.TextInput(attrs={"class": "input"}),
            "sort_order": forms.NumberInput(attrs={"class": "input"}),
        }


class NewsCategoryForm(forms.ModelForm):
    class Meta:
        model = NewsCategory
        fields = ("name", "code", "color", "sort_order")
        widgets = {
            "name": forms.TextInput(attrs={"class": "input"}),
            "code": forms.TextInput(attrs={"class": "input"}),
            "color": forms.TextInput(attrs={"class": "input", "type": "color"}),
            "sort_order": forms.NumberInput(attrs={"class": "input"}),
        }
