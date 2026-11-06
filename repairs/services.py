from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import Note, Status, StatusHistory, Ticket, TrackingLink
from .roles import can_correct

OPEN_STATUSES = (Status.RECEIVED, Status.DIAGNOSING, Status.REPAIRING)
NEXT_STATUSES = {
    Status.RECEIVED: (Status.DIAGNOSING, Status.CANCELLED),
    Status.DIAGNOSING: (Status.REPAIRING, Status.READY, Status.CANCELLED),
    Status.REPAIRING: (Status.READY, Status.CANCELLED),
    Status.READY: (Status.PICKED_UP, Status.CANCELLED),
}


@transaction.atomic
def create_ticket(*, actor, **fields):
    ticket = Ticket.objects.create(creator=actor, **fields)
    StatusHistory.objects.create(ticket=ticket, new_status=Status.RECEIVED, actor=actor)
    TrackingLink.objects.create(ticket=ticket)
    return ticket


def edit_ticket(ticket, fields):
    Ticket.objects.filter(pk=ticket.pk).update(**fields, updated_at=timezone.now())
    ticket.refresh_from_db()
    return ticket


@transaction.atomic
def transition(*, ticket_id, actor, expected_status, expected_version, new_status, reason=""):
    reason = reason.strip()
    if reason and not can_correct(actor):
        raise PermissionDenied
    if new_status not in Status.values or expected_status not in Status.values:
        raise ValidationError("Choose a valid status.")
    if new_status == expected_status:
        raise ValidationError("Choose a different status.")
    if not reason and new_status not in NEXT_STATUSES.get(expected_status, ()):
        raise ValidationError("This transition is unavailable. Admin corrections require a reason.")
    changed = Ticket.objects.filter(pk=ticket_id, status=expected_status, status_version=expected_version).update(
        status=new_status, status_version=F("status_version") + 1, updated_at=timezone.now()
    )
    if not changed:
        raise ValidationError("The status has changed. Refresh the ticket and try again.")
    StatusHistory.objects.create(ticket_id=ticket_id, previous_status=expected_status,
                                 new_status=new_status, actor=actor, correction_reason=reason)


@transaction.atomic
def rotate_link(ticket):
    # Take the ticket's write lock before touching links, including on SQLite.
    Ticket.objects.filter(pk=ticket.pk).update(updated_at=timezone.now())
    ticket.tracking_links.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())
    return TrackingLink.objects.create(ticket=ticket)


def add_note(ticket, actor, text, visibility=Note.Visibility.INTERNAL):
    return Note.objects.create(ticket=ticket, author=actor, text=text, visibility=visibility)
