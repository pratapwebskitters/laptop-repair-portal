from datetime import timedelta
from urllib.parse import urlsplit

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Category, Note, Status, Ticket


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AcceptanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tech = User.objects.create_user("tech", password="pass", is_staff=True)
        cls.admin = User.objects.create_user("manager", password="pass", is_staff=True)
        cls.tech.groups.add(Group.objects.get(name="Technicians"))
        cls.admin.groups.add(Group.objects.get(name="Shop admins"))
        cls.category = Category.objects.create(name="Diagnostics")
        cls.fields = {
            "customer_name": "Priya Example",
            "customer_contact": "private-contact@example.test",
            "laptop_make": "Lenovo",
            "laptop_model": "ThinkPad",
            "reported_issue": "private reported fault",
            "category": cls.category.pk,
            "estimated_pickup": (timezone.localdate() - timedelta(days=1)).isoformat(),
        }

    def setUp(self):
        self.assertTrue(self.client.login(username="tech", password="pass"))
        self.customer = Client()

    def intake(self):
        response = self.client.post(reverse("intake"), self.fields, follow=True)
        ticket = Ticket.objects.latest("pk")
        link = ticket.tracking_links.get()
        self.assertContains(response, reverse("track", args=[link.token]))
        self.assertContains(response, "Copy link")
        self.assertEqual(ticket.history.get().actor, self.tech)
        return ticket, link

    def change(self, ticket, new_status, **overrides):
        return self.client.post(f"/api/v1/tickets/{ticket.pk}/status/", {
            "expected_status": ticket.status, "expected_version": ticket.status_version,
            "new_status": new_status, **overrides,
        }, content_type="application/json")

    def test_intake_search_and_full_workflow(self):
        ticket, link = self.intake()
        for term in [ticket.reference.lower(), "priya"]:
            self.assertContains(self.client.get("/", {"q": term}), ticket.reference)
        self.assertContains(self.client.get("/"), "Overdue")
        public = reverse("track", args=[link.token])
        self.assertContains(self.customer.get(public), "Estimated pickup")
        self.assertContains(self.customer.get(public), "Received")
        for version, next_status in enumerate([Status.DIAGNOSING, Status.REPAIRING, Status.READY, Status.PICKED_UP]):
            previous = ticket.status
            response = self.client.post(reverse("ticket", args=[ticket.pk]), {
                "action": "status", "expected_status": previous,
                "expected_version": version, "new_status": next_status,
            })
            self.assertEqual(response.status_code, 302)
            ticket.refresh_from_db()
            self.assertEqual(ticket.status, next_status)
            self.assertContains(self.customer.get(public), next_status.label)
            event = ticket.history.get(previous_status=previous, new_status=next_status)
            self.assertEqual(event.actor, self.tech)
            self.assertIsNotNone(event.timestamp)
            if next_status == Status.READY:
                self.assertEqual(self.client.get("/").context["ready_count"], 1)
        self.assertEqual(ticket.history.count(), 5)

    def test_invalid_stale_and_admin_correction(self):
        ticket, _ = self.intake()
        self.assertEqual(self.change(ticket, Status.READY).status_code, 400)
        self.assertEqual(self.change(ticket, Status.READY, reason="Override").status_code, 403)
        self.assertEqual(self.change(ticket, Status.DIAGNOSING).status_code, 200)
        self.assertEqual(self.change(ticket, Status.CANCELLED).status_code, 400)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Status.DIAGNOSING)
        self.assertEqual(ticket.history.count(), 2)
        self.assertEqual(self.client.get("/admin/auth/user/").status_code, 403)
        self.assertEqual(self.client.get("/admin/repairs/category/").status_code, 403)

        self.client.force_login(self.admin)
        correction = reverse("admin:repairs_ticket_correct", args=[ticket.pk])
        self.assertContains(self.client.get(correction), "Record correction")
        self.assertEqual(self.client.post(correction, {
            "expected_status": ticket.status, "expected_version": ticket.status_version,
            "new_status": Status.RECEIVED, "reason": "Intake was reopened.",
        }).status_code, 302)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Status.RECEIVED)
        self.assertEqual(self.change(ticket, Status.CANCELLED, expected_version=0).status_code, 400)
        event = ticket.history.first()
        self.assertEqual(event.actor, self.admin)
        self.assertEqual(event.correction_reason, "Intake was reopened.")
        self.assertEqual(ticket.history.count(), 3)

    def test_public_allowlist_and_link_rotation(self):
        ticket, link = self.intake()
        endpoint = f"/api/v1/tickets/{ticket.pk}/"
        self.assertEqual(self.client.post(endpoint + "notes/", {"text": "Private work note"}).status_code, 201)
        self.assertEqual(ticket.notes.get().visibility, Note.Visibility.INTERNAL)
        self.assertEqual(self.client.post(reverse("ticket", args=[ticket.pk]), {
            "action": "note", "text": "Waiting for a part", "visibility": Note.Visibility.CUSTOMER,
        }).status_code, 302)

        web_url = reverse("track", args=[link.token])
        api_url = f"/api/v1/track/{link.token}/"
        for path in (web_url, api_url):
            response = self.customer.get(path)
            self.assertContains(response, "Waiting for a part")
            for secret in ("Private work note", self.fields["customer_contact"], self.fields["reported_issue"]):
                self.assertNotContains(response, secret)
            self.assertEqual(response["Cache-Control"], "no-store")
            self.assertEqual(response["Referrer-Policy"], "no-referrer")
        payload = self.customer.get(api_url).json()
        self.assertEqual(set(payload), {"reference", "status", "status_label", "estimated_pickup", "updates"})
        self.assertEqual(set(payload["updates"][0]), {"text", "timestamp"})

        rotated = self.client.post(endpoint + "tracking-link/")
        self.assertEqual(rotated.status_code, 200)
        new_path = urlsplit(rotated.json()["tracking_url"]).path
        self.assertContains(self.customer.get(new_path), ticket.reference)
        self.assertContains(self.customer.get("/api/v1" + new_path), ticket.reference)
        for old, unknown in ((web_url, "/track/invalid/"), (api_url, "/api/v1/track/invalid/")):
            revoked = self.customer.get(old)
            self.assertEqual(revoked.status_code, 404)
            self.assertEqual(revoked.content, self.customer.get(unknown).content)
        self.assertEqual(ticket.tracking_links.count(), 2)

    def test_staff_api_permissions_csrf_and_category_retention(self):
        endpoint = "/api/v1/tickets/"
        self.assertEqual(self.customer.get(endpoint).status_code, 403)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.tech)
        self.assertEqual(csrf.post(endpoint, self.fields).status_code, 403)
        csrf.get(reverse("intake"))
        response = csrf.post(endpoint, self.fields, content_type="application/json",
                             HTTP_X_CSRFTOKEN=csrf.cookies["csrftoken"].value)
        self.assertEqual(response.status_code, 201, response.content)
        ticket_id = response.json()["id"]
        self.assertEqual(self.client.get(endpoint, {"q": response.json()["reference"]}).json()["count"], 1)
        self.category.active = False
        self.category.save()
        self.assertEqual(self.client.post(endpoint, self.fields).status_code, 400)
        detail = f"{endpoint}{ticket_id}/"
        self.assertEqual(self.client.patch(detail, {"category": self.category.pk}, content_type="application/json").status_code, 200)
        self.assertEqual(self.client.patch(detail, {"status": "ready"}, content_type="application/json").status_code, 400)

    @override_settings(DEBUG=True)
    def test_seed_demo_is_repeatable(self):
        call_command("seed_demo")
        call_command("seed_demo")
        self.assertEqual(Ticket.objects.count(), 2)
        self.assertTrue(self.client.login(username="demo-tech", password="RepairDemo!2026"))
        self.assertEqual(self.client.get("/").context["overdue_count"], 1)

    def test_shop_admin_can_create_staff_and_categories(self):
        self.client.force_login(self.admin)
        response = self.client.post("/admin/auth/user/add/", {
            "username": "new-tech", "usable_password": "true", "password1": "Unusual!76210",
            "password2": "Unusual!76210", "is_active": "on",
            "groups": Group.objects.get(name="Technicians").pk,
        })
        self.assertEqual(response.status_code, 302)
        staff = User.objects.get(username="new-tech")
        self.assertTrue(staff.is_staff)
        self.assertFalse(staff.is_superuser)
        self.assertEqual(self.client.post("/admin/repairs/category/add/", {"name": "Screen", "active": "on"}).status_code, 302)

    @override_settings(TRACKING_PUBLIC_ORIGIN="https://repairs.example.test")
    def test_intake_emails_only_the_private_link_to_the_customer(self):
        email = "customer@example.test"
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("intake"), self.fields | {"customer_email": email})
        self.assertEqual(response.status_code, 302)
        ticket = Ticket.objects.latest("pk")
        link = ticket.tracking_links.get()
        self.assertEqual(ticket.customer_email, email)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [email])
        path = reverse("track", args=[link.token])
        self.assertIn(f"https://repairs.example.test{path}", mail.outbox[0].body)
        for public_path in (path, "/api/v1" + path):
            self.assertNotContains(self.customer.get(public_path), email)

    def test_api_emails_valid_addresses_only(self):
        endpoint = "/api/v1/tickets/"
        with self.captureOnCommitCallbacks(execute=True):
            invalid = self.client.post(endpoint, self.fields | {"customer_email": "not-an-email"})
            self.assertEqual(invalid.status_code, 400)
            self.assertFalse(Ticket.objects.exists())
            self.assertEqual(self.client.post(endpoint, self.fields).status_code, 201)
            result = self.client.post(endpoint, self.fields | {"customer_email": "customer@example.test"})
            self.assertEqual(result.status_code, 201)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(result.json()["tracking_url"], mail.outbox[0].body)

    @override_settings(RESEND_API_KEY="test-key", DEFAULT_FROM_EMAIL="repairs@example.test")
    def test_tracking_email_uses_https_provider_when_configured(self):
        import json
        from unittest.mock import patch

        with patch("repairs.services.urlopen") as send:
            send.return_value.__enter__.return_value.status = 200
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("intake"), self.fields | {"customer_email": "customer@example.test"},
                )
        self.assertEqual(response.status_code, 302)
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.resend.com/emails")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        payload = json.loads(request.data)
        self.assertEqual(payload["to"], ["customer@example.test"])
        self.assertEqual(payload["from"], "repairs@example.test")
        self.assertIn("/track/", payload["text"])
