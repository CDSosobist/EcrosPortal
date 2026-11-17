"""Тесты портала: доступ, справочник, новости, база знаний, заявки, уведомления."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Department, Employee, Role

from .models import (
    Attachment,
    KbCategory,
    KnowledgeArticle,
    NewsCategory,
    NewsPost,
    Notification,
    ServiceTicket,
    TicketComment,
)
from .templatetags.portal_extras import days_word, markdown_filter


class PortalTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.department = Department.objects.create(name="ИТ-отдел", code="it")
        cls.user = Employee.objects.create(
            username="i.ivanov",
            first_name="Иван",
            last_name="Иванов",
            job_title="Программист",
            department=cls.department,
            email="i.ivanov@ecros.local",
            phone_internal="1200",
        )
        cls.user.set_password("UserPass#2026")
        cls.user.save()
        cls.colleague = Employee.objects.create(
            username="p.petrov", first_name="Пётр", last_name="Петров", department=cls.department
        )
        cls.hr = Employee.objects.create(
            username="hr.user", first_name="Ольга", last_name="Кадрова", job_title="Кадровик"
        )
        role_hr = Role.objects.create(code="hr", name="Кадры")
        role_hr.members.add(cls.hr)

        cls.news_category = NewsCategory.objects.create(name="ИТ", code="it")
        cls.news = NewsPost.objects.create(
            title="Обновление портала",
            category=cls.news_category,
            summary="Коротко о новом",
            body="## Что нового\n\n- справочник\n- график отпусков",
            author=cls.hr,
        )
        cls.kb_category = KbCategory.objects.create(name="Инструкции", code="instructions")
        cls.article = KnowledgeArticle.objects.create(
            title="Как оформить отпуск",
            category=cls.kb_category,
            summary="Пошаговая инструкция",
            tags="отпуск, заявление",
            body="Откройте раздел «Отпуска» и нажмите «Новое заявление».",
            author=cls.hr,
        )
        cls.ticket = ServiceTicket.objects.create(
            subject="Не работает VPN",
            description="Ошибка 809",
            category=ServiceTicket.Category.IT,
            requester=cls.user,
        )


class AccessTest(PortalTestCase):
    def test_anonymous_is_redirected_to_login(self):
        for name in ("portal:dashboard", "portal:directory", "portal:tickets", "portal:kb_list"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302, name)
            self.assertIn("/login/", response.headers["Location"])

    def test_healthz_is_public(self):
        self.assertEqual(self.client.get("/healthz/").status_code, 200)

    def test_login_page_renders(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Вход в портал")

    def test_employee_can_open_core_pages(self):
        self.client.force_login(self.user)
        for name in (
            "portal:dashboard",
            "portal:directory",
            "portal:org_chart",
            "portal:news_list",
            "portal:kb_list",
            "portal:tickets",
            "portal:ticket_new",
            "portal:notifications",
            "accounts:profile",
        ):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)

    def test_dashboard_shows_personal_data(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("portal:dashboard"))
        self.assertContains(response, "Иванов")
        self.assertContains(response, "Оформить отпуск")

    def test_hr_cannot_be_blocked_from_management_sections(self):
        self.client.force_login(self.hr)
        self.assertEqual(self.client.get(reverse("accounts:manage_roles")).status_code, 200)


class DirectoryTest(PortalTestCase):
    def setUp(self):
        self.client.force_login(self.user)

    def test_search_by_last_name(self):
        response = self.client.get(reverse("portal:directory"), {"q": "Петров"})
        self.assertContains(response, "Петров")
        self.assertNotContains(response, "Кадрова")

    def test_search_by_phone(self):
        response = self.client.get(reverse("portal:directory"), {"q": "1200"})
        self.assertContains(response, "Иванов")

    def test_filter_by_department(self):
        other = Department.objects.create(name="Бухгалтерия", code="buh")
        Employee.objects.create(username="b.buh", last_name="Бухгалтерова", department=other)
        response = self.client.get(reverse("portal:directory"), {"department": self.department.pk})
        self.assertContains(response, "Иванов")
        self.assertNotContains(response, "Бухгалтерова")

    def test_employee_card_and_org_chart(self):
        self.assertEqual(
            self.client.get(reverse("portal:employee_detail", args=[self.user.pk])).status_code, 200
        )
        self.assertEqual(self.client.get(reverse("portal:org_chart")).status_code, 200)


class NewsAndKbTest(PortalTestCase):
    def setUp(self):
        self.client.force_login(self.user)

    def test_news_detail_counts_views(self):
        url = self.news.get_absolute_url()
        self.client.get(url)
        self.news.refresh_from_db()
        self.assertEqual(self.news.views_count, 1)
        self.assertContains(self.client.get(url), "Что нового")

    def test_article_search_by_tag(self):
        response = self.client.get(reverse("portal:kb_list"), {"q": "заявление"})
        self.assertContains(response, "Как оформить отпуск")

    def test_regular_employee_cannot_edit_content(self):
        self.assertEqual(self.client.get(reverse("portal:news_new")).status_code, 404)
        self.assertEqual(self.client.get(reverse("portal:kb_new")).status_code, 404)

    def test_hr_can_publish_news(self):
        self.client.force_login(self.hr)
        response = self.client.post(
            reverse("portal:news_new"),
            {
                "title": "Новая новость",
                "category": self.news_category.pk,
                "summary": "кратко",
                "body": "текст",
                "is_published": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(NewsPost.objects.filter(title="Новая новость").exists())


class TicketTest(PortalTestCase):
    def test_requester_sees_own_ticket(self):
        self.client.force_login(self.user)
        self.assertEqual(
            self.client.get(reverse("portal:ticket_detail", args=[self.ticket.pk])).status_code, 200
        )

    def test_colleague_cannot_see_foreign_ticket(self):
        self.client.force_login(self.colleague)
        response = self.client.get(reverse("portal:ticket_detail", args=[self.ticket.pk]))
        self.assertEqual(response.status_code, 404)

    def test_hr_sees_all_tickets(self):
        self.client.force_login(self.hr)
        response = self.client.get(reverse("portal:tickets"))
        self.assertContains(response, "Не работает VPN")

    def test_comment_notifies_requester(self):
        self.client.force_login(self.hr)
        self.client.post(
            reverse("portal:ticket_comment", args=[self.ticket.pk]),
            {"body": "Проверьте настройки прокси"},
        )
        self.assertTrue(
            Notification.objects.filter(recipient=self.user, kind=Notification.Kind.TICKET).exists()
        )

    def test_internal_comment_hidden_from_requester(self):
        TicketComment.objects.create(
            ticket=self.ticket, author=self.hr, body="внутренняя заметка", is_internal=True
        )
        TicketComment.objects.create(ticket=self.ticket, author=self.hr, body="публичный ответ")
        self.client.force_login(self.user)
        response = self.client.get(reverse("portal:ticket_detail", args=[self.ticket.pk]))
        self.assertContains(response, "публичный ответ")
        self.assertNotContains(response, "внутренняя заметка")

    def test_status_change_closes_ticket(self):
        self.client.force_login(self.hr)
        self.client.post(
            reverse("portal:ticket_update", args=[self.ticket.pk]),
            {"status": ServiceTicket.Status.RESOLVED, "assignee": self.hr.pk, "comment": "Готово"},
        )
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, ServiceTicket.Status.RESOLVED)
        self.assertIsNotNone(self.ticket.closed_at)

    def test_create_ticket_assigns_requester(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("portal:ticket_new"),
            {
                "category": ServiceTicket.Category.ACCESS,
                "priority": ServiceTicket.Priority.HIGH,
                "subject": "Нужен доступ к 1С",
                "description": "Требуется доступ на чтение",
            },
        )
        self.assertEqual(response.status_code, 302)
        created = ServiceTicket.objects.get(subject="Нужен доступ к 1С")
        self.assertEqual(created.requester, self.user)
        self.assertEqual(created.status, ServiceTicket.Status.NEW)


class NotificationTest(PortalTestCase):
    def test_mark_all_read(self):
        Notification.objects.create(recipient=self.user, title="A")
        Notification.objects.create(recipient=self.user, title="B")
        self.client.force_login(self.user)
        response = self.client.post(reverse("portal:notifications_read"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Notification.objects.filter(recipient=self.user, is_read=False).count(), 0)

    def test_mark_single_read_ajax(self):
        item = Notification.objects.create(recipient=self.user, title="Одно")
        other = Notification.objects.create(recipient=self.user, title="Другое")
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("portal:notifications_read"),
            {"id": item.pk},
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(response.json(), {"marked": 1})
        item.refresh_from_db()
        other.refresh_from_db()
        self.assertTrue(item.is_read)
        self.assertFalse(other.is_read)

    def test_unread_counter_in_dashboard(self):
        Notification.objects.create(recipient=self.user, title="Свежее")
        self.client.force_login(self.user)
        response = self.client.get(reverse("portal:dashboard"))
        self.assertEqual(response.context["unread_notifications"], 1)


class SearchTest(PortalTestCase):
    def test_finds_entities_across_sections(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("portal:search"), {"q": "отпуск"})
        self.assertContains(response, "Как оформить отпуск")
        self.assertEqual(response.context["total"] >= 1, True)

    def test_empty_query_shows_hint(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("portal:search"))
        self.assertContains(response, "Что можно найти")


class TemplateHelpersTest(TestCase):
    def test_markdown_renders_basic_markup(self):
        rendered = str(markdown_filter("## Заголовок\n\n- пункт\n\n**жирно** и *курсив*"))
        self.assertIn("<h3>Заголовок</h3>", rendered)
        self.assertIn("<li>пункт</li>", rendered)
        self.assertIn("<strong>жирно</strong>", rendered)
        self.assertIn("<em>курсив</em>", rendered)

    def test_markdown_escapes_html(self):
        rendered = str(markdown_filter('<script>alert("x")</script> <b>bold</b>'))
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("&lt;b&gt;bold&lt;/b&gt;", rendered)

    def test_markdown_links(self):
        rendered = str(markdown_filter("[почта](https://mail.ecros.local)"))
        self.assertIn('href="https://mail.ecros.local"', rendered)

    def test_days_word(self):
        self.assertEqual(days_word(1), "1 день")
        self.assertEqual(days_word(3), "3 дня")
        self.assertEqual(days_word(11), "11 дней")
        self.assertEqual(days_word(28), "28 дней")


class AttachmentTest(PortalTestCase):
    """Вложения заявок: загрузка, проверки, доступ и уборка файлов."""

    def setUp(self):
        self.storage_root = Path(tempfile.mkdtemp(prefix="ecros-attachments-"))
        self.addCleanup(shutil.rmtree, self.storage_root, ignore_errors=True)
        self.settings_override = override_settings(
            PORTAL_ATTACHMENTS_ROOT=self.storage_root,
            PORTAL_ATTACHMENT_MAX_MB=1,
            PORTAL_ATTACHMENT_MAX_FILES=3,
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.client.force_login(self.user)

    # -- вспомогательное --------------------------------------------------
    def make_file(self, name="отчёт.txt", content=b"hello world", content_type="text/plain"):
        return SimpleUploadedFile(name, content, content_type=content_type)

    def create_ticket_with_file(self, files=None, **extra):
        payload = {
            "category": ServiceTicket.Category.IIT if hasattr(ServiceTicket.Category, "IIT") else ServiceTicket.Category.IT,
            "priority": ServiceTicket.Priority.NORMAL,
            "subject": "Заявка с вложением",
            "description": "Подробности во вложении",
            "attachments": files if files is not None else [self.make_file()],
        }
        payload.update(extra)
        return self.client.post(reverse("portal:ticket_new"), payload)

    def disk_files(self):
        return [p for p in self.storage_root.rglob("*") if p.is_file()]

    # -- загрузка ---------------------------------------------------------
    def test_upload_on_ticket_creation(self):
        response = self.create_ticket_with_file()
        self.assertEqual(response.status_code, 302)
        ticket = ServiceTicket.objects.get(subject="Заявка с вложением")
        attachment = ticket.attachments.get()
        self.assertEqual(attachment.original_name, "отчёт.txt")
        self.assertEqual(attachment.content_type, "text/plain")
        self.assertEqual(attachment.size, 11)
        self.assertEqual(attachment.uploaded_by, self.user)
        self.assertIsNone(attachment.comment)
        self.assertFalse(attachment.is_internal)
        self.assertEqual(len(self.disk_files()), 1)
        self.assertIn(f"tickets/{ticket.pk}/", attachment.file.name)

    def test_several_files_at_once(self):
        files = [self.make_file("первый.txt", b"1"), self.make_file("второй.pdf", b"2", "application/pdf")]
        response = self.create_ticket_with_file(files=files)
        self.assertEqual(response.status_code, 302)
        ticket = ServiceTicket.objects.get(subject="Заявка с вложением")
        self.assertEqual(ticket.attachments.count(), 2)
        names = sorted(ticket.attachments.values_list("original_name", flat=True))
        self.assertEqual(names, ["второй.pdf", "первый.txt"])

    def test_too_large_file_rejected(self):
        big = self.make_file("большой.pdf", b"x" * (1024 * 1024 + 10), "application/pdf")
        response = self.create_ticket_with_file(files=[big])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "больше 1 МБ")
        self.assertFalse(ServiceTicket.objects.filter(subject="Заявка с вложением").exists())
        self.assertEqual(Attachment.objects.count(), 0)
        self.assertEqual(self.disk_files(), [])

    def test_forbidden_extension_rejected(self):
        response = self.create_ticket_with_file(files=[self.make_file("virus.exe", b"MZ")])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "запрещён")
        self.assertEqual(Attachment.objects.count(), 0)

    def test_empty_file_rejected(self):
        response = self.create_ticket_with_file(files=[self.make_file("пустой.txt", b"")])
        self.assertEqual(response.status_code, 200)
        # пустой файл отсекает уже стандартная валидация Django
        self.assertContains(response, "пуст")
        self.assertEqual(Attachment.objects.count(), 0)

    def test_files_count_limit(self):
        files = [self.make_file(f"файл{i}.txt", b"1") for i in range(4)]
        response = self.create_ticket_with_file(files=files)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "не более 3 файлов")
        self.assertEqual(Attachment.objects.count(), 0)

    def test_attach_to_existing_ticket(self):
        response = self.client.post(
            reverse("portal:ticket_attach", args=[self.ticket.pk]),
            {"files": [self.make_file("скрин.png", b"png-bytes", "image/png")]},
        )
        self.assertEqual(response.status_code, 302)
        attachment = self.ticket.attachments.get()
        self.assertEqual(attachment.original_name, "скрин.png")
        self.assertEqual(attachment.icon, "image")

    def test_comment_with_attachment(self):
        response = self.client.post(
            reverse("portal:ticket_comment", args=[self.ticket.pk]),
            {"body": "Смотрите вложение", "attachments": [self.make_file()]},
        )
        self.assertEqual(response.status_code, 302)
        comment = self.ticket.comments.get()
        attachment = comment.attachments.get()
        self.assertEqual(attachment.ticket, self.ticket)
        self.assertFalse(attachment.is_internal)

    def test_closed_ticket_rejects_requester_upload(self):
        self.ticket.status = ServiceTicket.Status.CLOSED
        self.ticket.save(update_fields=["status"])
        response = self.client.post(
            reverse("portal:ticket_attach", args=[self.ticket.pk]),
            {"files": [self.make_file()]},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.ticket.attachments.count(), 0)

    def test_agent_can_upload_to_closed_ticket(self):
        self.ticket.status = ServiceTicket.Status.CLOSED
        self.ticket.save(update_fields=["status"])
        self.client.force_login(self.hr)
        self.client.post(
            reverse("portal:ticket_attach", args=[self.ticket.pk]),
            {"files": [self.make_file("акт.txt", b"act")]},
        )
        self.assertEqual(self.ticket.attachments.count(), 1)

    # -- скачивание и права -----------------------------------------------
    def test_requester_downloads_file(self):
        self.create_ticket_with_file(files=[self.make_file("акт.txt", b"payload")])
        attachment = Attachment.objects.get()
        response = self.client.get(attachment.download_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"payload")
        disposition = response.headers["Content-Disposition"]
        self.assertIn("attachment", disposition)
        self.assertIn("utf-8", disposition)  # имя файла закодировано для кириллицы
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_colleague_cannot_download(self):
        self.create_ticket_with_file()
        attachment = Attachment.objects.get()
        self.client.force_login(self.colleague)
        self.assertEqual(self.client.get(attachment.download_url).status_code, 404)

    def test_agent_can_download(self):
        self.create_ticket_with_file()
        attachment = Attachment.objects.get()
        self.client.force_login(self.hr)
        self.assertEqual(self.client.get(attachment.download_url).status_code, 200)

    def test_anonymous_cannot_download(self):
        self.create_ticket_with_file()
        attachment = Attachment.objects.get()
        self.client.logout()
        response = self.client.get(attachment.download_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.headers["Location"])

    def test_internal_attachment_hidden_from_requester(self):
        self.client.force_login(self.hr)
        self.client.post(
            reverse("portal:ticket_attach", args=[self.ticket.pk]),
            {"files": [self.make_file("внутренний.txt", b"secret")], "is_internal": "on"},
        )
        attachment = self.ticket.attachments.get()
        self.assertTrue(attachment.is_internal)

        # исполнитель видит и скачивает
        self.assertEqual(self.client.get(attachment.download_url).status_code, 200)

        self.client.force_login(self.user)
        self.assertEqual(self.client.get(attachment.download_url).status_code, 404)
        page = self.client.get(reverse("portal:ticket_detail", args=[self.ticket.pk]))
        self.assertNotContains(page, "внутренний.txt")

    def test_internal_comment_attachment_is_internal(self):
        self.client.force_login(self.hr)
        self.client.post(
            reverse("portal:ticket_comment", args=[self.ticket.pk]),
            {
                "body": "внутренняя заметка",
                "is_internal": "on",
                "attachments": [self.make_file("заметка.txt", b"note")],
            },
        )
        attachment = Attachment.objects.get()
        self.assertTrue(attachment.is_internal)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(attachment.download_url).status_code, 404)

    def test_storage_url_is_forbidden(self):
        self.create_ticket_with_file()
        attachment = Attachment.objects.get()
        with self.assertRaises(ValueError):
            attachment.file.url

    # -- уборка файлов ----------------------------------------------------
    def test_file_deleted_with_ticket(self):
        self.create_ticket_with_file()
        ticket = ServiceTicket.objects.get(subject="Заявка с вложением")
        self.assertEqual(len(self.disk_files()), 1)
        ticket.delete()
        self.assertEqual(len(self.disk_files()), 0)
        self.assertEqual(Attachment.objects.count(), 0)

    def test_file_deleted_with_attachment(self):
        self.create_ticket_with_file()
        attachment = Attachment.objects.get()
        attachment.delete()
        self.assertEqual(self.disk_files(), [])

    def test_file_deleted_with_comment(self):
        self.client.post(
            reverse("portal:ticket_comment", args=[self.ticket.pk]),
            {"body": "с файлом", "attachments": [self.make_file()]},
        )
        self.assertEqual(len(self.disk_files()), 1)
        self.ticket.comments.all().delete()
        self.assertEqual(self.disk_files(), [])

    def test_ticket_list_shows_attachment_count(self):
        self.create_ticket_with_file()
        response = self.client.get(reverse("portal:tickets"))
        self.assertContains(response, "i-paperclip")
