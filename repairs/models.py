import secrets

from django.conf import settings
from django.db import models


class Category(models.Model):
    name = models.CharField(max_length=120, unique=True)
    active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Status(models.TextChoices):
    RECEIVED = "received", "Received"
    DIAGNOSING = "diagnosing", "Diagnosing"
    REPAIRING = "repairing", "Repairing"
    READY = "ready", "Ready for pickup"
    PICKED_UP = "picked_up", "Picked up"
    CANCELLED = "cancelled", "Cancelled"


class Ticket(models.Model):
    customer_name = models.CharField(max_length=150)
    customer_contact = models.CharField(max_length=250)
    laptop_make = models.CharField(max_length=100)
    laptop_model = models.CharField(max_length=100)
    reported_issue = models.TextField()
    category = models.ForeignKey(Category, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RECEIVED)
    status_version = models.PositiveBigIntegerField(default=0, editable=False)
    estimated_pickup = models.DateField(null=True, blank=True)
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        permissions = [("correct_ticket_status", "Can correct ticket status")]

    @property
    def reference(self):
        return f"REP-{self.pk:06d}"

    def __str__(self):
        return self.reference


class StatusHistory(models.Model):
    ticket = models.ForeignKey(Ticket, related_name="history", on_delete=models.PROTECT)
    previous_status = models.CharField(max_length=20, choices=Status.choices, blank=True)
    new_status = models.CharField(max_length=20, choices=Status.choices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    timestamp = models.DateTimeField(auto_now_add=True)
    correction_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-timestamp", "-pk"]


class Note(models.Model):
    class Visibility(models.TextChoices):
        INTERNAL = "internal", "Internal"
        CUSTOMER = "customer", "Customer-visible"

    ticket = models.ForeignKey(Ticket, related_name="notes", on_delete=models.PROTECT)
    text = models.TextField()
    visibility = models.CharField(max_length=10, choices=Visibility.choices, default=Visibility.INTERNAL)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp", "-pk"]


def tracking_token():
    return secrets.token_urlsafe(32)


class TrackingLink(models.Model):
    ticket = models.ForeignKey(Ticket, related_name="tracking_links", on_delete=models.PROTECT)
    token = models.CharField(max_length=43, unique=True, default=tracking_token, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["ticket"], condition=models.Q(revoked_at__isnull=True), name="one_active_tracking_link"
            )
        ]
