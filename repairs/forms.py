from django import forms
from django.db.models import Q

from .models import Category, Note, Status, Ticket
from .services import NEXT_STATUSES

TICKET_FIELDS = (
    "customer_name", "customer_contact", "laptop_make", "laptop_model",
    "reported_issue", "category", "estimated_pickup",
)


class TicketForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = TICKET_FIELDS
        widgets = {"estimated_pickup": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(
            Q(active=True) | Q(pk=self.instance.category_id)
        )


class StatusForm(forms.Form):
    expected_status = forms.ChoiceField(choices=Status.choices, widget=forms.HiddenInput)
    expected_version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    new_status = forms.ChoiceField(choices=Status.choices)
    reason = forms.CharField(required=False, max_length=2000, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, ticket, allow_correction=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["expected_status"].initial = ticket.status
        self.fields["expected_version"].initial = ticket.status_version
        if not allow_correction:
            self.fields.pop("reason")
            self.fields["new_status"].choices = [
                (value, label) for value, label in Status.choices
                if value in NEXT_STATUSES.get(ticket.status, ())
            ]


class NoteForm(forms.ModelForm):
    class Meta:
        model = Note
        fields = ("text", "visibility")
        widgets = {"text": forms.Textarea(attrs={"rows": 3})}
