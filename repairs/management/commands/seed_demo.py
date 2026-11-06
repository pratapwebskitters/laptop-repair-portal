from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from repairs.models import Category, Note, Status, Ticket
from repairs.services import add_note, create_ticket, transition


class Command(BaseCommand):
    help = "Create local demo staff and sample tickets."

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Demo data requires DJANGO_DEBUG=1.")
        for username, role in (("demo-tech", "Technicians"), ("demo-admin", "Shop admins")):
            user, created = User.objects.get_or_create(username=username, defaults={"is_staff": True})
            if created:
                user.set_password("RepairDemo!2026")
                user.save(update_fields=["password"])
                user.groups.add(Group.objects.get(name=role))

        actor = User.objects.get(username="demo-tech")
        hardware, _ = Category.objects.get_or_create(name="Hardware")
        power, _ = Category.objects.get_or_create(name="Power and charging")
        today = timezone.localdate()
        samples = [
            ("Asha Rao", "ThinkPad T14", "Battery does not charge", power, today - timedelta(days=1)),
            ("Dev Sen", "Latitude 5440", "Keyboard has intermittent keys", hardware, today + timedelta(days=2)),
        ]
        for name, model, issue, category, pickup in samples:
            make, model = model.split(" ", 1)
            if Ticket.objects.filter(creator=actor, customer_name=name, laptop_model=model).exists():
                continue
            ticket = create_ticket(
                actor=actor, customer_name=name, customer_contact="demo@example.test",
                laptop_make=make, laptop_model=model, reported_issue=issue,
                category=category, estimated_pickup=pickup,
            )
            if category == hardware:
                for version, previous, target in [
                    (0, Status.RECEIVED, Status.DIAGNOSING), (1, Status.DIAGNOSING, Status.READY),
                ]:
                    transition(ticket_id=ticket.pk, actor=actor, expected_status=previous,
                               expected_version=version, new_status=target)
                add_note(ticket, actor, "Your laptop is ready for pickup.", Note.Visibility.CUSTOMER)
        self.stdout.write(self.style.SUCCESS("Demo ready. Sign in with demo-tech or demo-admin."))
