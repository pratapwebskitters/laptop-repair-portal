import json
import logging
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.mail import send_mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from django.http import Http404
from django.urls import reverse
from django.utils import timezone

from .models import Estimate, Note, Status, StatusHistory, Ticket, TrackingLink
from .roles import can_correct

logger = logging.getLogger(__name__)

OPEN_STATUSES = (Status.RECEIVED, Status.DIAGNOSING, Status.REPAIRING)
NEXT_STATUSES = {
    Status.RECEIVED: (Status.DIAGNOSING, Status.CANCELLED),
    Status.DIAGNOSING: (Status.REPAIRING, Status.READY, Status.CANCELLED),
    Status.AWAITING_APPROVAL: (Status.REPAIRING, Status.CANCELLED),
    Status.REPAIRING: (Status.READY, Status.CANCELLED),
    Status.READY: (Status.PICKED_UP, Status.CANCELLED),
}


@transaction.atomic
def create_ticket(*, actor, request=None, **fields):
    ticket = Ticket.objects.create(creator=actor, **fields)
    StatusHistory.objects.create(ticket=ticket, new_status=Status.RECEIVED, actor=actor)
    link = TrackingLink.objects.create(ticket=ticket)
    if ticket.customer_email and request is not None:
        path = reverse("track", args=[link.token])
        origin = settings.TRACKING_PUBLIC_ORIGIN
        url = f"{origin}{path}" if origin else request.build_absolute_uri(path)
        transaction.on_commit(lambda: send_tracking_email(ticket, url))
    return ticket


def send_tracking_email(ticket, url):
    subject = f"Track your repair {ticket.reference}"
    message = (f"Your repair ticket {ticket.reference} is ready to track.\n\n{url}\n\n"
               "Keep this link private. It gives access to your repair status.")
    try:
        if settings.RESEND_API_KEY:
            request = Request(
                "https://api.resend.com/emails",
                data=json.dumps({
                    "from": settings.DEFAULT_FROM_EMAIL, "to": [ticket.customer_email],
                    "subject": subject, "text": message,
                }).encode(),
                headers={
                    "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                    "Content-Type": "application/json",
                    "User-Agent": "laptop-repair-portal/1.0",
                },
            )
            with urlopen(request, timeout=settings.EMAIL_TIMEOUT) as response:
                sent = 200 <= response.status < 300
        else:
            sent = send_mail(subject, message, None, [ticket.customer_email])
    except Exception:
        # Provider errors can contain email addresses or other private details.
        sent = 0
    if not sent:
        logger.warning("Tracking email could not be sent for %s.", ticket.reference)


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
    ticket = lock_ticket(ticket_id)
    if (ticket.status, ticket.status_version) != (expected_status, expected_version):
        raise ValidationError("The status has changed. Refresh the ticket and try again.")
    if new_status == Status.AWAITING_APPROVAL:
        raise ValidationError("Send an estimate to move the ticket to Awaiting approval.")
    if ticket.status == Status.AWAITING_APPROVAL and new_status not in NEXT_STATUSES[ticket.status]:
        raise ValidationError("Start the approved repair or cancel the ticket.")
    if new_status in (Status.REPAIRING, Status.READY, Status.PICKED_UP):
        estimate = current_estimate(ticket)
        if estimate and estimate.decision != Estimate.Decision.APPROVED:
            raise ValidationError("The customer must approve the current estimate before repair can proceed.")
    record_status(ticket, actor, new_status, reason)


def lock_ticket(ticket_id):
    # Acquire the write lock before reading related state, including on SQLite.
    if not Ticket.objects.filter(pk=ticket_id).update(status_version=F("status_version")):
        raise Http404
    return Ticket.objects.get(pk=ticket_id)


def record_status(ticket, actor, new_status, reason=""):
    previous_status = ticket.status
    ticket.status = new_status
    ticket.status_version += 1
    ticket.save(update_fields=["status", "status_version", "updated_at"])
    if previous_status != new_status:
        StatusHistory.objects.create(ticket=ticket, previous_status=previous_status,
                                     new_status=new_status, actor=actor, correction_reason=reason)



@transaction.atomic
def rotate_link(ticket):
    lock_ticket(ticket.pk)
    Ticket.objects.filter(pk=ticket.pk).update(updated_at=timezone.now())
    ticket.tracking_links.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())
    return TrackingLink.objects.create(ticket=ticket)


def add_note(ticket, actor, text, visibility=Note.Visibility.INTERNAL):
    return Note.objects.create(ticket=ticket, author=actor, text=text, visibility=visibility)


ESTIMATE_STATUSES = (*OPEN_STATUSES, Status.AWAITING_APPROVAL)


def current_estimate(ticket):
    return ticket.estimates.filter(sent_at__isnull=False).first()


def require_estimatable(ticket):
    if ticket.status not in ESTIMATE_STATUSES:
        raise ValidationError("Estimates can be sent while a repair is open or awaiting approval.")


@transaction.atomic
def save_estimate_draft(ticket_id, draft_id=None, **fields):
    ticket = lock_ticket(ticket_id)
    require_estimatable(ticket)
    draft = ticket.estimates.filter(sent_at__isnull=True).first()
    if draft_id is None:
        if draft:
            raise ValidationError("A draft already exists. Edit the existing draft.")
        latest = ticket.estimates.first()
        draft = Estimate(ticket=ticket, version=latest.version + 1 if latest else 1)
    elif not draft or draft.pk != draft_id:
        raise ValidationError("This draft is no longer editable.")
    for field, value in fields.items():
        setattr(draft, field, value)
    draft.full_clean()
    draft.save()
    return draft


@transaction.atomic
def send_estimate(ticket_id, draft_id, actor):
    ticket = lock_ticket(ticket_id)
    require_estimatable(ticket)
    draft = ticket.estimates.filter(pk=draft_id, sent_at__isnull=True).first()
    if not draft:
        raise ValidationError("This draft is no longer available to send.")
    draft.sent_at = timezone.now()
    draft.save(update_fields=["sent_at"])
    record_status(ticket, actor, Status.AWAITING_APPROVAL)
    return draft


@transaction.atomic
def decide_estimate(link, estimate_id, choice):
    ticket = lock_ticket(link.ticket_id)
    if not ticket.tracking_links.filter(pk=link.pk, revoked_at__isnull=True).exists():
        raise Http404
    estimate = current_estimate(ticket)
    if not estimate or estimate.pk != estimate_id:
        raise ValidationError("This estimate is no longer current. Review the latest estimate.")
    if choice not in Estimate.Decision.values:
        raise ValidationError("Choose Approve or Decline.")
    if estimate.decision == choice:
        return estimate
    if estimate.decision:
        raise ValidationError("A decision has already been recorded for this estimate.")
    if ticket.status != Status.AWAITING_APPROVAL:
        raise ValidationError("This estimate is closed for decisions.")
    estimate.decision = choice
    estimate.decided_at = timezone.now()
    estimate.save(update_fields=["decision", "decided_at"])
    return estimate
