from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse

from .forms import StatusForm
from .models import Category, StatusHistory, Ticket
from .roles import can_correct
from .services import transition


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "active")
    list_filter = ("active",)


class InspectOnly(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StatusHistory)
class StatusHistoryAdmin(InspectOnly):
    list_display = ("ticket", "previous_status", "new_status", "actor", "timestamp")
    list_select_related = ("ticket", "actor")


@admin.register(Ticket)
class TicketAdmin(InspectOnly):
    list_display = ("reference", "customer_name", "status", "estimated_pickup")
    readonly_fields = ("reference",)
    change_form_template = "repairs/admin_ticket.html"

    def get_urls(self):
        return [
            path("<int:ticket_id>/correct/", self.admin_site.admin_view(self.correct),
                 name="repairs_ticket_correct"),
        ] + super().get_urls()

    def render_change_form(self, request, context, *args, **kwargs):
        context["allow_correction"] = can_correct(request.user)
        return super().render_change_form(request, context, *args, **kwargs)

    def correct(self, request, ticket_id):
        if not can_correct(request.user):
            raise PermissionDenied
        ticket = get_object_or_404(Ticket, pk=ticket_id)
        form = StatusForm(request.POST or None, ticket=ticket, allow_correction=True)
        form.fields["reason"].required = True
        if request.method == "POST" and form.is_valid():
            try:
                transition(ticket_id=ticket.pk, actor=request.user, **form.cleaned_data)
            except ValidationError as error:
                form.add_error(None, error)
            else:
                self.message_user(request, "Status corrected.", messages.SUCCESS)
                return redirect(reverse("admin:repairs_ticket_change", args=[ticket.pk]))
        return render(request, "repairs/admin_correct.html", {
            **self.admin_site.each_context(request), "title": f"Correct {ticket.reference}", "form": form,
        })



class StaffUserFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "groups" in self.fields:
            self.fields["groups"].queryset = Group.objects.filter(name__in=["Technicians", "Shop admins"])
            self.fields["groups"].required = True


admin.site.unregister(User)


@admin.register(User)
class StaffUserAdmin(UserAdmin):
    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        return queryset.filter(is_superuser=False, groups__name__in=["Technicians", "Shop admins"]).distinct()

    def get_fieldsets(self, request, obj=None):
        if request.user.is_superuser:
            return super().get_fieldsets(request, obj)
        if obj is None:
            return [(None, {"fields": ("username", "usable_password", "password1", "password2", "groups", "is_active")})]
        return [(None, {"fields": ("username", "password", "groups", "is_active")})]

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if request.user.is_superuser:
            return form
        return type("StaffUserForm", (StaffUserFormMixin, form), {})

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            obj.is_staff = True
        super().save_model(request, obj, form, change)
