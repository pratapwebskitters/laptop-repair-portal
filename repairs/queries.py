import re

from django.db.models import Q

from .models import Ticket, TrackingLink


def find_tickets(params):
    tickets = Ticket.objects.select_related("category")
    term = params.get("q", "").strip()
    if term:
        match = re.fullmatch(r"REP-0*([1-9][0-9]{0,17})", term, re.IGNORECASE)
        query = Q(customer_name__icontains=term)
        if match:
            query |= Q(pk=int(match[1]))
        tickets = tickets.filter(query)
    if params.get("status"):
        tickets = tickets.filter(status=params["status"])
    category = params.get("category", "")
    if re.fullmatch(r"[0-9]{1,18}", category):
        tickets = tickets.filter(category_id=int(category))
    return tickets


def public_link(token):
    return TrackingLink.objects.select_related("ticket").filter(token=token, revoked_at__isnull=True).first()
