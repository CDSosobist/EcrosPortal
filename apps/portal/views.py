"""Страницы портала: главная, справочник, новости, база знаний, заявки, поиск."""
from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, F, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import Department, Employee
from apps.vacations import services as vacation_services
from apps.vacations.models import VacationRequest

from .forms import KbArticleForm, NewsForm, TicketCommentForm, TicketForm, TicketStatusForm
from .models import (
    KbCategory,
    KnowledgeArticle,
    NewsCategory,
    NewsPost,
    Notification,
    QuickLink,
    ServiceTicket,
    TicketComment,
)

PAGE_SIZE = 25


def _can_manage_content(user: Employee) -> bool:
    return user.is_portal_admin or user.is_hr


# ---------------------------------------------------------------------------
# Главная
# ---------------------------------------------------------------------------
@login_required
def dashboard(request):
    viewer = request.user
    today = timezone.localdate()
    period = vacation_services.current_period()
    year = period.year if period else today.year
    summary = vacation_services.balance_summary(viewer, year)

    my_requests = (
        VacationRequest.objects.filter(employee=viewer)
        .select_related("vacation_type", "period")
        .order_by("-start_date")[:5]
    )
    pending_my = list(vacation_services.pending_steps_for(viewer)[:5])
    upcoming_vacation = (
        VacationRequest.objects.filter(
            employee=viewer,
            status=VacationRequest.Status.APPROVED,
            end_date__gte=today,
        )
        .select_related("vacation_type")
        .order_by("start_date")
        .first()
    )

    news = list(
        NewsPost.objects.filter(is_published=True)
        .select_related("author", "category")[:4]
    )
    pinned = NewsPost.objects.filter(is_published=True, is_pinned=True).first()

    department_vacations = []
    if viewer.department_id:
        department_vacations = list(
            VacationRequest.objects.filter(
                employee__department_id=viewer.department_id,
                status=VacationRequest.Status.APPROVED,
                end_date__gte=today,
                start_date__lte=today + timedelta(days=45),
            )
            .select_related("employee", "vacation_type")
            .exclude(employee=viewer)
            .order_by("start_date")[:6]
        )

    my_tickets = ServiceTicket.objects.filter(
        requester=viewer, status__in=[ServiceTicket.Status.NEW, ServiceTicket.Status.IN_PROGRESS,
                                      ServiceTicket.Status.WAITING]
    ).order_by("-created_at")[:4]

    articles = KnowledgeArticle.objects.filter(is_published=True).order_by("-updated_at")[:5]
    team_absent_now = []
    if viewer.is_manager:
        team_absent_now = list(
            VacationRequest.objects.filter(
                employee__in=viewer.subordinates.all(),
                status=VacationRequest.Status.APPROVED,
                start_date__lte=today,
                end_date__gte=today,
            ).select_related("employee", "vacation_type")
        )

    return render(
        request,
        "portal/dashboard.html",
        {
            "today": today,
            "period": period,
            "year": year,
            "summary": summary,
            "my_requests": my_requests,
            "pending_my": pending_my,
            "upcoming_vacation": upcoming_vacation,
            "news": news,
            "pinned": pinned,
            "department_vacations": department_vacations,
            "my_tickets": my_tickets,
            "articles": articles,
            "quick_links": QuickLink.objects.filter(is_active=True),
            "team_absent_now": team_absent_now,
            "unread": Notification.objects.filter(recipient=viewer, is_read=False)[:5],
            "on_vacation_today": vacation_services.staff_on_vacation_today()[:12],
        },
    )


# ---------------------------------------------------------------------------
# Справочник сотрудников
# ---------------------------------------------------------------------------
@login_required
def directory(request):
    query = request.GET.get("q", "").strip()
    department_id = request.GET.get("department", "")
    status = request.GET.get("status", "")

    employees = Employee.objects.filter(is_active=True).select_related("department", "manager")
    if query:
        employees = employees.filter(
            Q(last_name__icontains=query)
            | Q(first_name__icontains=query)
            | Q(middle_name__icontains=query)
            | Q(username__icontains=query)
            | Q(email__icontains=query)
            | Q(job_title__icontains=query)
            | Q(phone_internal__icontains=query)
            | Q(phone_mobile__icontains=query)
        )
    if department_id.isdigit():
        department = Department.objects.filter(pk=int(department_id)).first()
        if department is not None:
            employees = employees.filter(department_id__in=department.descendant_ids(include_self=True))
    if status == Employee.Status.ON_VACATION:
        employees = employees.filter(status=Employee.Status.ON_VACATION)

    paginator = Paginator(employees.order_by("last_name", "first_name"), PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "portal/directory.html",
        {
            "page_obj": page,
            "employees": page.object_list,
            "query": query,
            "department_id": department_id,
            "status": status,
            "total": paginator.count,
            "departments": Department.objects.filter(is_active=True).order_by("sort_order", "name"),
        },
    )


@login_required
def employee_detail(request, pk: int):
    employee = get_object_or_404(
        Employee.objects.select_related("department", "manager", "department__parent"), pk=pk
    )
    year = timezone.localdate().year
    vacations = (
        VacationRequest.objects.filter(
            employee=employee, status=VacationRequest.Status.APPROVED, end_date__gte=timezone.localdate()
        )
        .select_related("vacation_type")
        .order_by("start_date")[:10]
    )
    return render(
        request,
        "portal/employee_detail.html",
        {
            "employee": employee,
            "subordinates": employee.subordinates.filter(is_active=True).select_related("department"),
            "manager_chain": employee.manager_chain()[:5],
            "roles": employee.roles.all(),
            "vacations": vacations,
            "summary": vacation_services.balance_summary(employee, year),
            "year": year,
        },
    )


@login_required
def org_chart(request):
    departments = list(
        Department.objects.filter(is_active=True).annotate(staff=Count("employees", filter=Q(employees__is_active=True)))
        .select_related("head")
        .order_by("sort_order", "name")
    )
    by_parent: dict[int | None, list[Department]] = {}
    for department in departments:
        by_parent.setdefault(department.parent_id, []).append(department)

    def build(parent_id, depth=0):
        nodes = []
        for department in by_parent.get(parent_id, []):
            nodes.append(
                {
                    "department": department,
                    "staff": department.staff,
                    "head": department.head,
                    "children": build(department.pk, depth + 1),
                    "depth": depth,
                }
            )
        return nodes

    return render(
        request,
        "portal/org_chart.html",
        {
            "tree": build(None),
            "total_staff": Employee.objects.filter(is_active=True).count(),
            "total_departments": len(departments),
        },
    )


# ---------------------------------------------------------------------------
# Новости
# ---------------------------------------------------------------------------
@login_required
def news_list(request):
    category = request.GET.get("category", "")
    posts = NewsPost.objects.filter(is_published=True).select_related("author", "category")
    if category:
        posts = posts.filter(category__code=category)
    paginator = Paginator(posts, 12)
    page = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "portal/news_list.html",
        {
            "page_obj": page,
            "posts": page.object_list,
            "categories": NewsCategory.objects.annotate(total=Count("posts")),
            "category": category,
            "can_edit": _can_manage_content(request.user),
        },
    )


@login_required
def news_detail(request, slug: str):
    post = get_object_or_404(NewsPost.objects.select_related("author", "category"), slug=slug)
    NewsPost.objects.filter(pk=post.pk).update(views_count=F("views_count") + 1)
    related = (
        NewsPost.objects.filter(is_published=True)
        .exclude(pk=post.pk)
        .select_related("category")[:4]
    )
    return render(
        request,
        "portal/news_detail.html",
        {"post": post, "related": related, "can_edit": _can_manage_content(request.user)},
    )


# ---------------------------------------------------------------------------
# База знаний
# ---------------------------------------------------------------------------
@login_required
def kb_list(request):
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "")
    articles = KnowledgeArticle.objects.filter(is_published=True).select_related("category", "author")
    if query:
        articles = articles.filter(
            Q(title__icontains=query) | Q(body__icontains=query) | Q(tags__icontains=query)
        )
    if category:
        articles = articles.filter(category__code=category)
    paginator = Paginator(articles, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "portal/kb_list.html",
        {
            "page_obj": page,
            "articles": page.object_list,
            "categories": KbCategory.objects.all(),
            "query": query,
            "category": category,
            "can_edit": _can_manage_content(request.user),
        },
    )


@login_required
def kb_detail(request, slug: str):
    article = get_object_or_404(
        KnowledgeArticle.objects.select_related("category", "author"), slug=slug, is_published=True
    )
    KnowledgeArticle.objects.filter(pk=article.pk).update(views_count=F("views_count") + 1)
    related = (
        KnowledgeArticle.objects.filter(is_published=True, category=article.category)
        .exclude(pk=article.pk)
        .order_by("-updated_at")[:5]
    )
    return render(
        request,
        "portal/kb_detail.html",
        {"article": article, "related": related, "can_edit": _can_manage_content(request.user)},
    )


# ---------------------------------------------------------------------------
# Заявки в поддержку
# ---------------------------------------------------------------------------
@login_required
def tickets(request):
    viewer = request.user
    is_agent = _can_manage_content(viewer)
    qs = ServiceTicket.objects.select_related("requester", "assignee")
    if not is_agent:
        qs = qs.filter(Q(requester=viewer) | Q(assignee=viewer))
    status = request.GET.get("status", "")
    category = request.GET.get("category", "")
    if status:
        qs = qs.filter(status=status)
    if category:
        qs = qs.filter(category=category)
    paginator = Paginator(qs, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "portal/tickets.html",
        {
            "page_obj": page,
            "tickets": page.object_list,
            "status": status,
            "category": category,
            "is_agent": is_agent,
            "open_count": ServiceTicket.objects.filter(
                status__in=[ServiceTicket.Status.NEW, ServiceTicket.Status.IN_PROGRESS,
                            ServiceTicket.Status.WAITING]
            ).count(),
        },
    )


@login_required
def ticket_create(request):
    if request.method == "POST":
        form = TicketForm(request.POST)
        if form.is_valid():
            ticket = form.save(commit=False)
            ticket.requester = request.user
            if _can_manage_content(request.user):
                ticket.status = ServiceTicket.Status.IN_PROGRESS
                ticket.assignee = request.user
            ticket.save()
            messages.success(request, f"Заявка {ticket.number} создана. Исполнитель свяжется с вами.")
            return redirect("portal:ticket_detail", pk=ticket.pk)
        messages.error(request, "Проверьте поля формы.")
    else:
        form = TicketForm()
    return render(request, "portal/ticket_form.html", {"form": form})


@login_required
def ticket_detail(request, pk: int):
    viewer = request.user
    ticket = get_object_or_404(
        ServiceTicket.objects.select_related("requester", "assignee"), pk=pk
    )
    is_agent = _can_manage_content(viewer) or ticket.assignee_id == viewer.pk
    if not (is_agent or ticket.requester_id == viewer.pk):
        raise Http404("Заявка не найдена")
    comments = ticket.comments.select_related("author").all()
    if not is_agent:
        comments = comments.filter(is_internal=False)
    return render(
        request,
        "portal/ticket_detail.html",
        {
            "ticket": ticket,
            "comments": comments,
            "comment_form": TicketCommentForm(),
            "status_form": TicketStatusForm(
                initial={"status": ticket.status, "assignee": ticket.assignee_id}
            ),
            "is_agent": is_agent,
        },
    )


@login_required
@require_POST
def ticket_comment(request, pk: int):
    viewer = request.user
    ticket = get_object_or_404(ServiceTicket, pk=pk)
    is_agent = _can_manage_content(viewer) or ticket.assignee_id == viewer.pk
    if not (is_agent or ticket.requester_id == viewer.pk):
        raise Http404("Заявка не найдена")
    form = TicketCommentForm(request.POST)
    if form.is_valid():
        comment = form.save(commit=False)
        comment.ticket = ticket
        comment.author = viewer
        comment.is_internal = bool(form.cleaned_data.get("is_internal")) and is_agent
        comment.save()
        if is_agent and ticket.requester_id != viewer.pk:
            Notification.push(
                ticket.requester,
                title=f"Ответ по заявке {ticket.number}",
                body=comment.body[:300],
                url=ticket.get_absolute_url(),
                kind=Notification.Kind.TICKET,
                actor=viewer,
            )
        messages.success(request, "Комментарий добавлен.")
    else:
        messages.error(request, "Комментарий не может быть пустым.")
    return redirect("portal:ticket_detail", pk=ticket.pk)


@login_required
@require_POST
def ticket_update(request, pk: int):
    viewer = request.user
    ticket = get_object_or_404(ServiceTicket, pk=pk)
    if not (_can_manage_content(viewer) or ticket.assignee_id == viewer.pk):
        raise Http404("Заявка не найдена")
    form = TicketStatusForm(request.POST)
    if form.is_valid():
        ticket.status = form.cleaned_data["status"]
        ticket.assignee = form.cleaned_data["assignee"] or ticket.assignee
        if ticket.status in {ServiceTicket.Status.RESOLVED, ServiceTicket.Status.CLOSED}:
            ticket.closed_at = timezone.now()
        ticket.save()
        comment = form.cleaned_data.get("comment", "").strip()
        if comment:
            TicketComment.objects.create(ticket=ticket, author=viewer, body=comment)
        Notification.push(
            ticket.requester,
            title=f"Статус заявки {ticket.number}: {ticket.get_status_display()}",
            body=comment[:300],
            url=ticket.get_absolute_url(),
            kind=Notification.Kind.TICKET,
            actor=viewer,
        )
        messages.success(request, "Заявка обновлена.")
    else:
        messages.error(request, "Проверьте данные формы.")
    return redirect("portal:ticket_detail", pk=ticket.pk)


# ---------------------------------------------------------------------------
# Поиск
# ---------------------------------------------------------------------------
@login_required
def search(request):
    query = request.GET.get("q", "").strip()
    results = {"employees": [], "news": [], "kb": [], "tickets": [], "vacations": []}
    if query:
        results["employees"] = list(
            Employee.objects.filter(
                Q(last_name__icontains=query)
                | Q(first_name__icontains=query)
                | Q(job_title__icontains=query)
                | Q(email__icontains=query),
                is_active=True,
            ).select_related("department")[:8]
        )
        results["news"] = list(
            NewsPost.objects.filter(
                Q(title__icontains=query) | Q(body__icontains=query), is_published=True
            )[:6]
        )
        results["kb"] = list(
            KnowledgeArticle.objects.filter(
                Q(title__icontains=query) | Q(body__icontains=query) | Q(tags__icontains=query),
                is_published=True,
            )[:6]
        )
        viewer = request.user
        ticket_qs = ServiceTicket.objects.filter(
            Q(subject__icontains=query) | Q(description__icontains=query)
        )
        if not _can_manage_content(viewer):
            ticket_qs = ticket_qs.filter(Q(requester=viewer) | Q(assignee=viewer))
        results["tickets"] = list(ticket_qs[:6])
        results["vacations"] = list(
            vacation_services.request_queryset_for(viewer).filter(
                Q(employee__last_name__icontains=query) | Q(employee__first_name__icontains=query)
            )[:6]
        )
    total = sum(len(items) for items in results.values())
    return render(request, "portal/search.html", {"query": query, "results": results, "total": total})


# ---------------------------------------------------------------------------
# Уведомления
# ---------------------------------------------------------------------------
@login_required
def notifications(request):
    qs = Notification.objects.filter(recipient=request.user).select_related("actor")
    unread_only = request.GET.get("unread") == "1"
    if unread_only:
        qs = qs.filter(is_read=False)
    paginator = Paginator(qs, 30)
    page = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "portal/notifications.html",
        {"page_obj": page, "items": page.object_list, "unread_only": unread_only},
    )


@login_required
@require_POST
def notifications_read(request):
    notification_id = request.POST.get("id")
    qs = Notification.objects.filter(recipient=request.user, is_read=False)
    if notification_id:
        qs = qs.filter(pk=notification_id)
    count = qs.update(is_read=True)
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"marked": count})
    messages.success(request, f"Отмечено прочитанными: {count}.")
    return redirect(request.POST.get("next") or "portal:notifications")


# ---------------------------------------------------------------------------
# Управление контентом
# ---------------------------------------------------------------------------
@login_required
def news_edit(request, pk: int | None = None):
    if not _can_manage_content(request.user):
        raise Http404("Недостаточно прав")
    instance = get_object_or_404(NewsPost, pk=pk) if pk else None
    if request.method == "POST":
        form = NewsForm(request.POST, instance=instance)
        if form.is_valid():
            post = form.save(commit=False)
            if post.author_id is None:
                post.author = request.user
            if not post.published_at:
                post.published_at = timezone.now()
            post.save()
            messages.success(request, "Новость сохранена.")
            return redirect("portal:news_detail", slug=post.slug)
        messages.error(request, "Проверьте поля формы.")
    else:
        form = NewsForm(instance=instance)
    return render(request, "portal/content_form.html", {"form": form, "kind": "Новость", "instance": instance})


@login_required
def kb_edit(request, pk: int | None = None):
    if not _can_manage_content(request.user):
        raise Http404("Недостаточно прав")
    instance = get_object_or_404(KnowledgeArticle, pk=pk) if pk else None
    if request.method == "POST":
        form = KbArticleForm(request.POST, instance=instance)
        if form.is_valid():
            article = form.save(commit=False)
            if article.author_id is None:
                article.author = request.user
            article.save()
            messages.success(request, "Статья сохранена.")
            return redirect("portal:kb_detail", slug=article.slug)
        messages.error(request, "Проверьте поля формы.")
    else:
        form = KbArticleForm(instance=instance)
    return render(
        request, "portal/content_form.html", {"form": form, "kind": "Статья базы знаний", "instance": instance}
    )


# ---------------------------------------------------------------------------
# Ошибки
# ---------------------------------------------------------------------------
def error_403(request, exception=None):
    return render(request, "errors/403.html", status=403)


def error_404(request, exception=None):
    return render(request, "errors/404.html", status=404)
