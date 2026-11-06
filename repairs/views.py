from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_safe

from .forms import NoteForm, StatusForm, TicketForm
from .models import Category, Note, Status, Ticket
from .queries import find_tickets, public_link
from .roles import can_correct, is_repair_staff
from .services import NEXT_STATUSES, OPEN_STATUSES, add_note, create_ticket, edit_ticket, rotate_link, transition


def staff_view(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not is_repair_staff(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped


@staff_view
@require_safe
def dashboard(request):
    today = timezone.localdate()
    open_tickets = Ticket.objects.filter(status__in=OPEN_STATUSES)
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "repairs/dashboard.html", {
        "open_count": open_tickets.count(),
        "overdue_count": open_tickets.filter(estimated_pickup__lt=today).count(),
        "ready_count": Ticket.objects.filter(status=Status.READY).count(),
        "page": Paginator(find_tickets(params), 20).get_page(request.GET.get("page")),
        "filters": params,
        "statuses": Status.choices,
        "categories": Category.objects.all(),
        "today": today,
        "open_statuses": OPEN_STATUSES,
    })


@staff_view
@require_http_methods(["GET", "POST"])
def intake(request):
    form = TicketForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        ticket = create_ticket(actor=request.user, **form.cleaned_data)
        return redirect("ticket", pk=ticket.pk)
    return render(request, "repairs/intake.html", {"form": form})


@staff_view
@require_http_methods(["GET", "POST"])
def detail(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    action = request.POST.get("action")
    can_fix = can_correct(request.user)
    edit = TicketForm(request.POST if action == "edit" else None, instance=ticket)
    status = StatusForm(request.POST if action == "status" else None, ticket=ticket, allow_correction=can_fix)
    note = NoteForm(request.POST if action == "note" else None)
    if request.method == "POST":
        if action == "edit" and edit.is_valid():
            edit_ticket(ticket, edit.cleaned_data)
            return redirect("ticket", pk=pk)
        if action == "status" and status.is_valid():
            try:
                transition(ticket_id=pk, actor=request.user, **status.cleaned_data)
            except ValidationError as error:
                status.add_error(None, error)
            else:
                return redirect("ticket", pk=pk)
        if action == "note" and note.is_valid():
            add_note(ticket, request.user, **note.cleaned_data)
            return redirect("ticket", pk=pk)
        if action == "rotate":
            rotate_link(ticket)
            return redirect("ticket", pk=pk)
    link = ticket.tracking_links.get(revoked_at__isnull=True)
    return render(request, "repairs/detail.html", {
        "ticket": ticket, "edit": edit, "status": status, "note": note,
        "show_status": can_fix or ticket.status in NEXT_STATUSES or action == "status",
        "tracking_url": request.build_absolute_uri(reverse("track", args=[link.token])),
        "history": ticket.history.select_related("actor"), "notes": ticket.notes.select_related("author"),
    })


@require_safe
def track(request, token):
    link = public_link(token)
    if link is None:
        return HttpResponse("Tracking link not found.", status=404, content_type="text/plain")
    ticket = link.ticket
    return render(request, "repairs/track.html", {
        "reference": ticket.reference, "status": ticket.get_status_display(),
        "estimated_pickup": ticket.estimated_pickup,
        "updates": ticket.notes.filter(visibility=Note.Visibility.CUSTOMER).values("text", "timestamp"),
    })
