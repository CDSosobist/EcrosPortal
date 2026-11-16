"""Тесты графика отпусков: дни, балансы, маршруты, согласование."""
from __future__ import annotations

from datetime import date, timedelta

from django.test import TestCase, override_settings

from apps.accounts.models import Department, Employee, Role
from apps.portal.models import Notification

from . import services
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

YEAR = 2030


class VacationTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sales = Department.objects.create(name="Отдел продаж", code="sales")
        cls.hr_department = Department.objects.create(name="Отдел кадров", code="hr-dep")
        cls.directorate = Department.objects.create(name="Коммерческая дирекция", code="commerce")
        cls.sales.parent = cls.directorate
        cls.sales.save()

        cls.director = Employee.objects.create(
            username="director", last_name="Директоров", first_name="Дмитрий",
            job_title="Коммерческий директор", department=cls.directorate, phone_internal="1001",
        )
        cls.manager = Employee.objects.create(
            username="manager", last_name="Руководителев", first_name="Роман",
            job_title="Руководитель отдела продаж", department=cls.sales,
            manager=cls.director, phone_internal="1002",
        )
        cls.worker = Employee.objects.create(
            username="worker", last_name="Работников", first_name="Иван",
            job_title="Менеджер по продажам", department=cls.sales,
            manager=cls.manager, phone_internal="1003",
        )
        cls.hr = Employee.objects.create(
            username="hr", last_name="Кадрова", first_name="Ольга",
            job_title="Руководитель отдела кадров", department=cls.hr_department,
            manager=cls.director, phone_internal="1004",
        )
        cls.sales.head = cls.manager
        cls.sales.save()
        cls.directorate.head = cls.director
        cls.directorate.save()
        cls.hr_department.head = cls.hr
        cls.hr_department.save()

        cls.role_hr = Role.objects.create(code="hr", name="Кадры", head=cls.hr)
        cls.role_hr.members.add(cls.hr)
        cls.role_director = Role.objects.create(code="director", name="Директора", head=cls.director)
        cls.role_director.members.add(cls.director)

        cls.annual = VacationType.objects.create(
            code="annual", name="Ежегодный оплачиваемый отпуск", default_days=28,
            consumes_balance=True, requires_approval=True,
        )
        cls.unpaid = VacationType.objects.create(
            code="unpaid", name="Без сохранения заработной платы",
            consumes_balance=False, requires_approval=True,
        )
        cls.sick = VacationType.objects.create(
            code="sick", name="Отгул", consumes_balance=False, requires_approval=False,
        )

        cls.period = SchedulePeriod.objects.create(
            year=YEAR, status=SchedulePeriod.Status.COLLECTING
        )

        cls.standard_chain = ApprovalChainTemplate.objects.create(
            code="standard", name="Стандартный", is_default=True, priority=100
        )
        ApprovalChainStep.objects.create(
            template=cls.standard_chain, order=1, title="Согласование руководителя",
            approver_kind=ApprovalChainStep.ApproverKind.MANAGER,
        )
        ApprovalChainStep.objects.create(
            template=cls.standard_chain, order=2, title="Проверка отдела кадров",
            approver_kind=ApprovalChainStep.ApproverKind.HR,
        )

    def make_balance(self, employee=None, days=28, carried=0):
        employee = employee or self.worker
        return VacationBalance.objects.update_or_create(
            employee=employee, year=YEAR,
            defaults={"entitled_days": days, "carried_over_days": carried},
        )[0]

    def make_request(self, *, employee=None, start=None, days=10, vacation_type=None, submit=True):
        employee = employee or self.worker
        vacation_type = vacation_type or self.annual
        start = start or date(YEAR, 6, 1)
        end = start + timedelta(days=days - 1)
        request_obj = VacationRequest.objects.create(
            employee=employee,
            period=self.period,
            vacation_type=vacation_type,
            start_date=start,
            end_date=end,
            days_count=services.count_vacation_days(start, end),
        )
        if submit:
            services.submit_request(request_obj, actor=employee)
            request_obj.refresh_from_db()
        return request_obj


class DaysCalculationTest(VacationTestCase):
    def test_calendar_days_without_holidays(self):
        Holiday.objects.create(date=date(YEAR, 6, 12), name="День России")
        self.assertEqual(services.count_vacation_days(date(YEAR, 6, 1), date(YEAR, 6, 10)), 10)
        # 12 июня — праздник, в отпуск не входит
        self.assertEqual(services.count_vacation_days(date(YEAR, 6, 1), date(YEAR, 6, 14)), 13)

    def test_zero_for_invalid_range(self):
        self.assertEqual(services.count_vacation_days(date(YEAR, 6, 10), date(YEAR, 6, 1)), 0)

    def test_working_days_respect_weekends_and_holidays(self):
        Holiday.objects.create(date=date(YEAR, 6, 3), name="Праздник")
        # 1 июня 2030 — суббота; считаем неделю
        working = services.count_working_days(date(YEAR, 6, 1), date(YEAR, 6, 7))
        self.assertEqual(working, 4)  # пн-чт, пятница праздник убрана


class BalanceTest(VacationTestCase):
    def test_balance_tracks_used_and_pending(self):
        self.make_balance(days=28)
        request_obj = self.make_request(days=10)
        summary = services.balance_summary(self.worker, YEAR)
        self.assertEqual(summary["total"], 28)
        self.assertEqual(summary["pending"], request_obj.days_count)
        self.assertEqual(summary["used"], 0)
        self.assertEqual(summary["available"], 28 - request_obj.days_count)

        services.approve_step(request_obj.current_step, self.manager)
        services.approve_step(request_obj.current_step, self.hr)
        request_obj.refresh_from_db()
        summary = services.balance_summary(self.worker, YEAR)
        self.assertEqual(request_obj.status, VacationRequest.Status.APPROVED)
        self.assertEqual(summary["used"], request_obj.days_count)
        self.assertEqual(summary["pending"], 0)

    def test_balance_created_on_demand(self):
        balance = services.balance_for(self.worker, YEAR)
        self.assertIsNotNone(balance)
        self.assertEqual(balance.entitled_days, 28)

    def test_unpaid_vacation_does_not_consume_balance(self):
        self.make_balance(days=10)
        request_obj = self.make_request(days=10, vacation_type=self.unpaid)
        services.approve_step(request_obj.current_step, self.manager)
        services.approve_step(request_obj.current_step, self.hr)
        summary = services.balance_summary(self.worker, YEAR)
        self.assertEqual(summary["used"], 0)
        self.assertEqual(summary["available"], 10)


class RouteResolutionTest(VacationTestCase):
    def test_default_chain_resolves_manager_and_hr(self):
        route = services.resolve_route(self.worker, self.annual)
        self.assertEqual(route.template, self.standard_chain)
        approvers = [step.approver for step in route.usable_steps]
        self.assertEqual(approvers, [self.manager, self.hr])

    def test_department_chain_has_priority(self):
        department_chain = ApprovalChainTemplate.objects.create(
            code="sales", name="Отдел продаж", department=self.sales, priority=10
        )
        ApprovalChainStep.objects.create(
            template=department_chain, order=1, title="Директор",
            approver_kind=ApprovalChainStep.ApproverKind.DIRECTOR,
        )
        route = services.resolve_route(self.worker, self.annual)
        self.assertEqual(route.template, department_chain)
        self.assertEqual(route.usable_steps[0].approver, self.director)

    def test_role_chain_applies_to_directors(self):
        role_chain = ApprovalChainTemplate.objects.create(
            code="directors", name="Директора", priority=20
        )
        role_chain.applies_to_roles.set([self.role_director])
        ApprovalChainStep.objects.create(
            template=role_chain, order=1, title="Кадры",
            approver_kind=ApprovalChainStep.ApproverKind.HR,
        )
        route = services.resolve_route(self.director, self.annual)
        self.assertEqual(route.template, role_chain)

    def test_manager_step_falls_back_to_department_head(self):
        loner = Employee.objects.create(
            username="loner", last_name="Безруков", first_name="Пётр", department=self.sales
        )
        route = services.resolve_route(loner, self.annual)
        self.assertEqual(route.usable_steps[0].approver, self.manager)

    def test_step_skipped_when_approver_unknown(self):
        orphan = Employee.objects.create(username="orphan", last_name="Сиротин", first_name="Олег")
        route = services.resolve_route(orphan, self.annual)
        self.assertEqual(route.steps[0].approver, None)
        self.assertEqual([s.approver for s in route.usable_steps], [self.hr])

    def test_no_chain_raises_clear_error(self):
        ApprovalChainTemplate.objects.all().update(is_active=False)
        with self.assertRaisesMessage(services.VacationError, "Маршрут согласования не настроен"):
            self.make_request()


class SubmissionTest(VacationTestCase):
    def test_submit_creates_steps_and_notifies_first_approver(self):
        self.make_balance()
        request_obj = self.make_request(days=10)
        self.assertEqual(request_obj.status, VacationRequest.Status.PENDING)
        self.assertEqual(request_obj.steps.count(), 2)
        self.assertEqual(request_obj.current_step.approver, self.manager)
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.manager, kind=Notification.Kind.APPROVAL
            ).exists()
        )
        self.assertEqual(request_obj.days_count, 10)
        self.assertIsNotNone(request_obj.submitted_at)

    def test_request_without_approval_requirement_is_approved(self):
        self.make_balance()
        request_obj = self.make_request(days=3, vacation_type=self.sick)
        self.assertEqual(request_obj.status, VacationRequest.Status.APPROVED)
        self.assertEqual(request_obj.steps.count(), 0)

    def test_empty_route_is_approved_with_audit(self):
        self.make_balance()
        empty_role = Role.objects.create(code="empty", name="Без участников")
        self.standard_chain.steps.all().delete()
        ApprovalChainStep.objects.create(
            template=self.standard_chain,
            order=1,
            title="Согласование роли без участников",
            approver_kind=ApprovalChainStep.ApproverKind.ROLE,
            role=empty_role,
        )
        request_obj = self.make_request(days=5)
        self.assertEqual(request_obj.status, VacationRequest.Status.APPROVED)
        self.assertEqual(request_obj.steps.count(), 0)

    def test_overlapping_request_rejected(self):
        self.make_balance()
        self.make_request(start=date(YEAR, 6, 1), days=10)
        with self.assertRaisesMessage(services.VacationError, "пересекается"):
            self.make_request(start=date(YEAR, 6, 5), days=5)

    def test_insufficient_balance_rejected(self):
        self.make_balance(days=4)
        with self.assertRaisesMessage(services.VacationError, "Недостаточно дней"):
            self.make_request(days=10)

    def test_closed_period_rejected(self):
        self.make_balance()
        SchedulePeriod.objects.filter(pk=self.period.pk).update(
            status=SchedulePeriod.Status.APPROVED
        )
        with self.assertRaisesMessage(services.VacationError, "Приём заявлений"):
            self.make_request(days=5)

    def test_min_notice_rule(self):
        today = services.timezone.localdate()
        start = today + timedelta(days=1)
        if start.year != today.year:  # конец декабря — проверяем на следующем годе
            start = date(today.year + 1, 1, 2)
        period = SchedulePeriod.objects.create(year=start.year, status=SchedulePeriod.Status.COLLECTING)
        VacationBalance.objects.update_or_create(
            employee=self.worker, year=start.year, defaults={"entitled_days": 28}
        )
        with override_settings(VACATION_MIN_NOTICE_DAYS=3):
            with self.assertRaisesMessage(services.VacationError, "менее"):
                services.validate_request(
                    self.worker, period, self.annual, start, start + timedelta(days=4),
                    actor=self.worker,
                )


class VacationViewsTest(VacationTestCase):
    """Интерфейс заявлений: форма, отправка, права доступа."""

    def setUp(self):
        self.make_balance()
        self.client.force_login(self.worker)

    def test_form_contains_period_and_route(self):
        response = self.client.get("/vacations/new/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "График на год")
        self.assertContains(response, "Маршрут согласования")

    def test_submit_creates_pending_request(self):
        response = self.client.post(
            "/vacations/new/",
            {
                "period": self.period.pk,
                "vacation_type": self.annual.pk,
                "start_date": date(YEAR, 7, 1),
                "end_date": date(YEAR, 7, 10),
                "employee_comment": "Через портал",
            },
        )
        self.assertEqual(response.status_code, 302)
        request_obj = VacationRequest.objects.get(employee=self.worker)
        self.assertEqual(request_obj.status, VacationRequest.Status.PENDING)
        self.assertEqual(request_obj.steps.count(), 2)  # руководитель → кадры
        self.assertEqual(request_obj.period, self.period)

    def test_invalid_dates_show_error(self):
        response = self.client.post(
            "/vacations/new/",
            {
                "period": self.period.pk,
                "vacation_type": self.annual.pk,
                "start_date": date(YEAR, 7, 10),
                "end_date": date(YEAR, 7, 1),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Дата окончания не может быть раньше")

    def test_employee_cannot_see_foreign_request(self):
        other_request = self.make_request(employee=self.director, start=date(YEAR, 9, 1), days=5)
        response = self.client.get(f"/vacations/requests/{other_request.pk}/")
        self.assertEqual(response.status_code, 403)

    def test_manager_sees_subordinate_request(self):
        request_obj = self.make_request(employee=self.worker, days=5)
        self.client.force_login(self.manager)
        response = self.client.get(f"/vacations/requests/{request_obj.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Согласовать")

    def test_hr_management_sections_require_role(self):
        self.assertEqual(self.client.get("/vacations/manage/").status_code, 403)
        self.client.force_login(self.hr)
        self.assertEqual(self.client.get("/vacations/manage/").status_code, 200)


class ApprovalFlowTest(VacationTestCase):
    def setUp(self):
        self.make_balance()
        self.request_obj = self.make_request(days=10)

    def test_full_chain_approves_request(self):
        first = self.request_obj.current_step
        services.approve_step(first, self.manager, "Согласовано")
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, VacationRequest.Status.PENDING)
        self.assertEqual(self.request_obj.current_step.approver, self.hr)
        self.assertTrue(
            Notification.objects.filter(recipient=self.hr, kind=Notification.Kind.APPROVAL).exists()
        )

        services.approve_step(self.request_obj.current_step, self.hr, "Проверено")
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, VacationRequest.Status.APPROVED)
        self.assertIsNotNone(self.request_obj.decided_at)
        self.assertTrue(
            Notification.objects.filter(recipient=self.worker, kind=Notification.Kind.VACATION).exists()
        )
        self.assertEqual(
            list(self.request_obj.steps.values_list("status", flat=True)),
            [VacationRequestStep.Status.APPROVED, VacationRequestStep.Status.APPROVED],
        )

    def test_outsider_cannot_approve(self):
        with self.assertRaisesMessage(services.VacationError, "не являетесь согласующим"):
            services.approve_step(self.request_obj.current_step, self.director)

    def test_second_step_cannot_be_approved_before_first(self):
        hr_step = self.request_obj.steps.order_by("-order").first()
        with self.assertRaisesMessage(services.VacationError, "другой участник"):
            services.approve_step(hr_step, self.hr)

    def test_reject_requires_reason_and_closes_request(self):
        with self.assertRaisesMessage(services.VacationError, "причину"):
            services.reject_step(self.request_obj.current_step, self.manager, "   ")

        services.reject_step(self.request_obj.current_step, self.manager, "Не хватает людей")
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, VacationRequest.Status.REJECTED)
        self.assertEqual(self.request_obj.decision_comment, "Не хватает людей")
        self.assertEqual(
            self.request_obj.steps.filter(status=VacationRequestStep.Status.CANCELLED).count(), 1
        )
        self.assertTrue(
            Notification.objects.filter(recipient=self.worker, kind=Notification.Kind.VACATION).exists()
        )

    def test_owner_can_cancel_pending_request(self):
        services.cancel_request(self.request_obj, self.worker, "Поменялись планы")
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, VacationRequest.Status.CANCELLED)
        self.assertEqual(
            self.request_obj.steps.filter(status=VacationRequestStep.Status.PENDING).count(), 0
        )

    def test_stranger_cannot_cancel(self):
        with self.assertRaisesMessage(services.VacationError, "только его автор"):
            services.cancel_request(self.request_obj, self.director)

    def test_hr_can_cancel_approved_request(self):
        services.approve_step(self.request_obj.current_step, self.manager)
        services.approve_step(self.request_obj.current_step, self.hr)
        services.cancel_request(self.request_obj, self.hr, "Отзыв по производственной необходимости")
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, VacationRequest.Status.CANCELLED)

    def test_pending_steps_shows_only_current_approver(self):
        self.assertEqual(list(services.pending_steps_for(self.manager)), [self.request_obj.current_step])
        self.assertEqual(list(services.pending_steps_for(self.hr)), [])

        services.approve_step(self.request_obj.current_step, self.manager)
        self.assertEqual(list(services.pending_steps_for(self.manager)), [])
        self.assertEqual(len(list(services.pending_steps_for(self.hr))), 1)

    def test_schedule_contains_only_approved(self):
        self.assertEqual(services.schedule_entries(YEAR).count(), 0)
        services.approve_step(self.request_obj.current_step, self.manager)
        services.approve_step(self.request_obj.current_step, self.hr)
        entries = services.schedule_entries(YEAR)
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries[0], self.request_obj)

    def test_editable_only_until_submitted(self):
        self.assertFalse(self.request_obj.is_editable)
        draft = VacationRequest.objects.create(
            employee=self.worker,
            period=self.period,
            vacation_type=self.annual,
            start_date=date(YEAR, 8, 1),
            end_date=date(YEAR, 8, 5),
            days_count=5,
        )
        self.assertTrue(draft.is_editable)
        self.assertEqual(draft.status, VacationRequest.Status.DRAFT)
