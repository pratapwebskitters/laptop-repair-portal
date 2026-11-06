from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from .models import Category, Note, Status, Ticket
from .services import add_note, create_ticket, rotate_link, save_estimate_draft, send_estimate


class EstimateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tech = User.objects.create_user("estimate-tech", is_staff=True)
        cls.tech.groups.add(Group.objects.get(name="Technicians"))
        category = Category.objects.create(name="Display")
        cls.ticket = create_ticket(
            actor=cls.tech, category=category, customer_name="Sample", customer_contact="Private contact",
            laptop_make="Lenovo", laptop_model="T14", reported_issue="Private intake report",
        )
        cls.fields = {
            "diagnosis": "Display cable is damaged", "proposed_work": "Replace the cable",
            "total_price": "1500.50", "currency": "INR",
        }

    def setUp(self):
        self.client.force_login(self.tech)
        self.customer = Client(enforce_csrf_checks=True)
        self.link = self.ticket.tracking_links.get()
        self.track = reverse("track", args=[self.link.token])
        self.public_api = "/api/v1" + self.track
        self.detail = reverse("ticket", args=[self.ticket.pk])
        self.api = f"/api/v1/tickets/{self.ticket.pk}/"

    def draft(self, **fields):
        response = self.client.post(self.api + "estimates/", self.fields | fields, content_type="application/json")
        self.assertEqual(response.status_code, 201, response.content)
        return self.ticket.estimates.get(pk=response.json()["id"])

    def send(self, draft):
        response = self.client.post(f"{self.api}estimates/{draft.pk}/send/")
        self.assertEqual(response.status_code, 200, response.content)
        self.ticket.refresh_from_db()
        draft.refresh_from_db()
        return draft

    def decide(self, estimate, choice, *, path=None):
        path = path or self.track
        if "csrftoken" not in self.customer.cookies:
            self.customer.get(self.track)
        return self.customer.post(path, {
            "estimate_id": estimate.pk, "choice": choice,
            "csrfmiddlewaretoken": self.customer.cookies["csrftoken"].value,
        })

    def transition(self, target, *, browser=False, **extra):
        self.ticket.refresh_from_db()
        payload = {"expected_status": self.ticket.status, "expected_version": self.ticket.status_version,
                   "new_status": target, **extra}
        if browser:
            return self.client.post(self.detail, payload | {"action": "status"})
        return self.client.post(self.api + "status/", payload, content_type="application/json")

    def test_e01_staff_saves_edits_and_sends_locked_estimate(self):
        self.assertRedirects(self.client.post(self.detail, self.fields | {"action": "estimate_draft"}), self.detail)
        draft = self.ticket.estimates.get()
        self.assertIsNone(draft.sent_at)
        edit = self.fields | {"action": "estimate_draft", "draft_id": draft.pk, "total_price": "1650.75"}
        self.assertRedirects(self.client.post(self.detail, edit), self.detail)
        self.assertRedirects(self.client.post(self.detail, {"action": "estimate_send", "draft_id": draft.pk}), self.detail)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Status.AWAITING_APPROVAL)
        self.assertEqual(self.ticket.history.filter(new_status=Status.AWAITING_APPROVAL).count(), 1)
        self.assertContains(self.customer.get(self.track), "INR 1650.75")
        self.assertContains(self.customer.get(self.track), self.fields["proposed_work"])
        path = f"{self.api}estimates/{draft.pk}/"
        self.assertEqual(self.client.patch(path, {"total_price": "1"}, content_type="application/json").status_code, 400)
        self.assertContains(self.client.post(self.detail, edit), "This draft is no longer editable.")
        draft.refresh_from_db()
        self.assertEqual(draft.total_price, Decimal("1650.75"))

    def test_e02_rejects_bad_prices_and_missing_scope(self):
        for invalid in ({"total_price": "-1"}, {"total_price": "1.123"}, {"total_price": "NaN"},
                        {"proposed_work": "   "}, {"currency": "$"}):
            data = self.fields | invalid
            with self.subTest(invalid=invalid):
                response = self.client.post(self.api + "estimates/", data, content_type="application/json")
                self.assertEqual(response.status_code, 400)
                response = self.client.post(self.detail, data | {"action": "estimate_draft"})
                self.assertContains(response, "errorlist")
        self.assertFalse(self.ticket.estimates.exists())
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Status.RECEIVED)

    def test_e03_approval_unlocks_repair_and_records_version(self):
        estimate = self.send(self.draft())
        self.assertEqual(self.transition(Status.REPAIRING).status_code, 400)
        self.assertRedirects(self.decide(estimate, "approved"), self.track)
        self.assertContains(self.customer.get(self.track), "Approved")
        self.assertContains(self.client.get(self.detail), "Approved · Ready to start")
        self.assertContains(self.client.get("/", {"status": Status.AWAITING_APPROVAL}), "Approved · Ready to start")
        record = self.client.get(self.api).json()["estimates"][0]
        self.assertEqual((record["version"], record["decision"]), (1, "approved"))
        self.assertIsNotNone(record["decided_at"])
        self.assertEqual(self.transition(Status.REPAIRING, browser=True).status_code, 302)

    def test_secure_browser_submission_preserves_no_referrer(self):
        estimate = self.send(self.draft())
        page = self.customer.get(self.track, secure=True)
        self.assertEqual(page["Referrer-Policy"], "no-referrer")
        response = self.customer.post(self.track, {
            "estimate_id": estimate.pk, "choice": "approved",
            "csrfmiddlewaretoken": self.customer.cookies["csrftoken"].value,
        }, secure=True, HTTP_ORIGIN="https://testserver", HTTP_ACCEPT="application/json")
        self.assertEqual(response.json(), {"decision": "approved"})
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_e04_decline_blocks_ui_api_and_admin_override(self):
        estimate = self.send(self.draft())
        self.assertRedirects(self.decide(estimate, "declined"), self.track)
        self.assertContains(self.client.get(self.detail), "Declined")
        self.assertContains(self.transition(Status.REPAIRING, browser=True), "must approve")
        self.assertEqual(self.transition(Status.REPAIRING).status_code, 400)
        admin = User.objects.create_superuser("estimate-admin")
        self.client.force_login(admin)
        self.assertEqual(self.transition(Status.REPAIRING, reason="Override").status_code, 400)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Status.AWAITING_APPROVAL)

    def test_e05_repeat_and_conflict_preserve_first_decision(self):
        estimate = self.send(self.draft())
        self.assertEqual(self.decide(estimate, "approved").status_code, 302)
        estimate.refresh_from_db()
        first_timestamp = estimate.decided_at
        self.assertEqual(self.decide(estimate, "approved").status_code, 302)
        self.assertContains(self.decide(estimate, "declined"), "already been recorded", status_code=400)
        estimate.refresh_from_db()
        self.assertEqual((estimate.decision, estimate.decided_at), ("approved", first_timestamp))

    def test_e06_old_form_cannot_decide_revision(self):
        first = self.send(self.draft())
        self.customer.get(self.track)
        second = self.send(self.draft(proposed_work="Replace cable and connector"))
        self.assertContains(self.decide(first, "approved"), "no longer current", status_code=400)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.decision, second.decision), ("", ""))

    def test_e07_revision_during_repair_requires_fresh_approval(self):
        first = self.send(self.draft())
        self.decide(first, "approved")
        self.assertEqual(self.transition(Status.REPAIRING).status_code, 200)
        second_draft = self.draft()
        patch = self.client.patch(f"{self.api}estimates/{second_draft.pk}/", {"total_price": "1999.90"},
                                  content_type="application/json")
        self.assertEqual(patch.status_code, 200, patch.content)
        second = self.send(second_draft)
        self.assertEqual(self.ticket.status, Status.AWAITING_APPROVAL)
        self.assertEqual(second.version, 2)
        self.assertEqual(second.total_price, Decimal("1999.90"))
        self.assertEqual(self.transition(Status.REPAIRING).status_code, 400)
        first.refresh_from_db()
        self.assertEqual(first.decision, "approved")
        self.assertContains(self.client.get(self.detail), "Version 1 · Superseded · Approved")
        self.assertEqual(self.decide(second, "approved").status_code, 302)
        self.assertEqual(self.transition(Status.REPAIRING).status_code, 200)

    def test_e08_revoked_link_and_foreign_estimate_rejected(self):
        estimate = self.send(self.draft())
        other = create_ticket(actor=self.tech, category=self.ticket.category)
        other_estimate = save_estimate_draft(other.pk, **self.fields)
        send_estimate(other.pk, other_estimate.pk, self.tech)
        self.assertEqual(self.decide(other_estimate, "approved").status_code, 400)
        replacement = rotate_link(self.ticket)
        self.assertEqual(self.decide(estimate, "approved").status_code, 404)
        estimate.refresh_from_db()
        other_estimate.refresh_from_db()
        self.assertEqual((estimate.decision, other_estimate.decision), ("", ""))
        self.assertEqual(self.decide(estimate, "approved", path=reverse("track", args=[replacement.token])).status_code, 302)

    def test_e09_public_only_shows_current_sent_scope(self):
        add_note(self.ticket, self.tech, "Private work log", Note.Visibility.INTERNAL)
        first = self.draft(diagnosis="First version scope")
        self.assertNotContains(self.customer.get(self.track), "First version scope")
        self.assertNotIn("estimate", self.customer.get(self.public_api).json())
        self.send(first)
        self.draft(diagnosis="Private draft revision")
        self.assertContains(self.customer.get(self.track), "First version scope")
        second = self.ticket.estimates.first()
        self.send(second)
        third = self.send(self.draft(diagnosis="Current customer scope"))
        self.draft(diagnosis="Unsent content")
        for url in (self.track, self.public_api):
            response = self.customer.get(url)
            self.assertContains(response, "Current customer scope")
            for secret in ("First version scope", "Private draft revision", "Unsent content", "Private work log",
                           self.tech.username, self.ticket.customer_contact):
                self.assertNotContains(response, secret)
        payload = self.customer.get(self.public_api).json()["estimate"]
        self.assertEqual(payload["version"], third.version)
        self.assertEqual(set(payload), {*self.fields, "version", "sent_at", "decision", "decided_at"})

    def test_e10_staff_permissions_and_csrf_required(self):
        path = self.api + "estimates/"
        self.assertEqual(self.customer.post(path, self.fields).status_code, 403)
        staff = Client(enforce_csrf_checks=True)
        staff.force_login(self.tech)
        self.assertEqual(staff.post(path, self.fields).status_code, 403)
        self.assertFalse(self.ticket.estimates.exists())
        estimate = self.send(self.draft())
        self.assertEqual(self.customer.post(self.track, {"estimate_id": estimate.pk, "choice": "approved"}).status_code, 403)
        estimate.refresh_from_db()
        self.assertFalse(estimate.decision)

    def test_e11_filter_shows_latest_decision_until_status_changes(self):
        estimate = self.send(self.draft())
        filters = {"status": Status.AWAITING_APPROVAL}
        self.assertContains(self.client.get("/", filters), "Estimate pending")
        self.assertEqual(self.client.get("/api/v1/tickets/", filters).json()["count"], 1)
        self.decide(estimate, "declined")
        self.assertContains(self.client.get("/", filters), "Estimate declined")
        self.assertEqual(self.transition(Status.CANCELLED).status_code, 200)
        self.assertNotContains(self.client.get("/", filters), self.ticket.reference)
        self.assertEqual(self.client.get("/api/v1/tickets/", filters).json()["count"], 0)


class EstimateUpgradeTests(TransactionTestCase):
    def test_e12_existing_ticket_and_link_survive_upgrade(self):
        previous = [("repairs", "0003_ticket_status_version")]
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        executor.migrate(previous)
        old = executor.loader.project_state(previous).apps
        user = old.get_model("auth", "User").objects.create(username="existing-tech", is_staff=True)
        user.groups.add(old.get_model("auth", "Group").objects.get(name="Technicians"))
        category = old.get_model("repairs", "Category").objects.create(name="Existing category")
        ticket = old.get_model("repairs", "Ticket").objects.create(
            creator_id=user.pk, category_id=category.pk, customer_name="Existing customer",
        )
        link = old.get_model("repairs", "TrackingLink").objects.create(ticket_id=ticket.pk)
        old.get_model("repairs", "StatusHistory").objects.create(
            ticket_id=ticket.pk, actor_id=user.pk, new_status="received",
        )

        MigrationExecutor(connection).migrate(latest)
        self.assertContains(self.client.get(reverse("track", args=[link.token])), "Received")
        staff = Client()
        staff.force_login(User.objects.get(pk=user.pk))
        current = Ticket.objects.get(pk=ticket.pk)
        self.assertFalse(current.estimates.exists())
        for target in (Status.DIAGNOSING, Status.REPAIRING, Status.READY, Status.PICKED_UP):
            response = staff.post(f"/api/v1/tickets/{current.pk}/status/", {
                "expected_status": current.status, "expected_version": current.status_version, "new_status": target,
            }, content_type="application/json")
            self.assertEqual(response.status_code, 200, response.content)
            current.refresh_from_db()
        self.assertEqual(current.history.count(), 5)
        self.assertContains(self.client.get(reverse("track", args=[link.token])), "Picked up")
