from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import OuterRef, Subquery
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_safe

from .forms import DecisionForm, EstimateForm, NoteForm, SendEstimateForm, StatusForm, TicketForm
from .models import Category, Estimate, Note, Status, Ticket
from .queries import find_tickets, public_link
from .roles import can_correct, is_repair_staff
from .services import (
    ESTIMATE_STATUSES, NEXT_STATUSES, OPEN_STATUSES, add_note, create_ticket, current_estimate,
    decide_estimate, edit_ticket, rotate_link, save_estimate_draft, send_estimate, transition,
)


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
    sent = Estimate.objects.filter(ticket_id=OuterRef("pk"), sent_at__isnull=False)
    tickets = find_tickets(params).annotate(estimate_decision=Subquery(sent.values("decision")[:1]))
    return render(request, "repairs/dashboard.html", {
        "open_count": open_tickets.count(),
        "overdue_count": open_tickets.filter(estimated_pickup__lt=today).count(),
        "ready_count": Ticket.objects.filter(status=Status.READY).count(),
        "page": Paginator(tickets, 20).get_page(request.GET.get("page")),
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
        ticket = create_ticket(actor=request.user, request=request, **form.cleaned_data)
        return redirect("ticket", pk=ticket.pk)
    return render(request, "repairs/intake.html", {"form": form})


@staff_view
@require_http_methods(["GET", "POST"])
def detail(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    action = request.POST.get("action")
    sections = {
        "status": "Repair status", "estimates": "Repair estimates", "details": "Ticket details",
        "tracking": "Tracking link", "history": "Status history", "notes": "Notes",
    }
    section = request.GET.get("section", "status")
    if section not in sections:
        section = "status"
    detail_url = reverse("ticket", args=[pk])
    return_url = f"{detail_url}?section={section}" if "section" in request.GET else detail_url
    can_fix = can_correct(request.user)
    edit = TicketForm(request.POST if action == "edit" else None, instance=ticket)
    status = StatusForm(request.POST if action == "status" else None, ticket=ticket, allow_correction=can_fix)
    note = NoteForm(request.POST if action == "note" else None)
    draft = ticket.estimates.filter(sent_at__isnull=True).first()
    estimate_form = EstimateForm(
        request.POST if action == "estimate_draft" else None,
        instance=draft, initial={"draft_id": draft.pk if draft else None},
    )
    send_form = SendEstimateForm(
        request.POST if action == "estimate_send" else None,
        initial={"draft_id": draft.pk if draft else None},
    )
    if request.method == "POST":
        if action == "estimate_draft" and estimate_form.is_valid():
            try:
                save_estimate_draft(ticket.pk, **estimate_form.cleaned_data)
            except ValidationError as error:
                estimate_form.add_error(None, error)
            else:
                return redirect(return_url)
        if action == "estimate_send" and send_form.is_valid():
            try:
                send_estimate(ticket.pk, actor=request.user, **send_form.cleaned_data)
            except ValidationError as error:
                send_form.add_error(None, error)
            else:
                return redirect(return_url)
        if action == "edit" and edit.is_valid():
            edit_ticket(ticket, edit.cleaned_data)
            return redirect(return_url)
        if action == "status" and status.is_valid():
            try:
                transition(ticket_id=pk, actor=request.user, **status.cleaned_data)
            except ValidationError as error:
                status.add_error(None, error)
            else:
                return redirect(return_url)
        if action == "note" and note.is_valid():
            add_note(ticket, request.user, **note.cleaned_data)
            return redirect(return_url)
        if action == "rotate":
            rotate_link(ticket)
            return redirect(return_url)
    link = ticket.tracking_links.get(revoked_at__isnull=True)
    return render(request, "repairs/detail.html", {
        "sections": sections, "section": section,
        "ticket": ticket, "edit": edit, "status": status, "note": note,
        "show_status": can_fix or ticket.status in NEXT_STATUSES or action == "status",
        "tracking_url": request.build_absolute_uri(reverse("track", args=[link.token])),
        "estimate_form": estimate_form, "send_form": send_form, "draft": draft,
        "show_estimate_form": ticket.status in ESTIMATE_STATUSES or action in ("estimate_draft", "estimate_send"),
        "sent_estimates": ticket.estimates.filter(sent_at__isnull=False),
        "history": ticket.history.select_related("actor"), "notes": ticket.notes.select_related("author"),
    })


@require_http_methods(["GET", "HEAD", "POST"])
def track(request, token):
    link = public_link(token)
    if link is None:
        return HttpResponse("Tracking link not found.", status=404, content_type="text/plain")
    error = None
    if request.method == "POST":
        wants_json = request.headers.get("Accept") == "application/json"
        form = DecisionForm(request.POST)
        if form.is_valid():
            try:
                decide_estimate(link, **form.cleaned_data)
            except Http404:
                return HttpResponse("Tracking link not found.", status=404, content_type="text/plain")
            except ValidationError as exc:
                error = exc.messages[0]
            else:
                if wants_json:
                    return JsonResponse({"decision": form.cleaned_data["choice"]})
                return redirect("track", token=token)
        else:
            error = "Choose Approve or Decline for the current estimate."
        if wants_json:
            return JsonResponse({"error": error}, status=400)
    ticket = link.ticket
    if error:
        ticket.refresh_from_db()
    estimate = current_estimate(ticket)
    return render(request, "repairs/track.html", {
        "reference": ticket.reference, "status": ticket.get_status_display(),
        "estimate": estimate, "decision_error": error,
        "can_decide": estimate and not estimate.decision and ticket.status == Status.AWAITING_APPROVAL,
        "estimated_pickup": ticket.estimated_pickup,
        "updates": ticket.notes.filter(visibility=Note.Visibility.CUSTOMER).values("text", "timestamp"),
    }, status=400 if error else 200)
