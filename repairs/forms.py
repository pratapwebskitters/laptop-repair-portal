from django import forms
from django.db.models import Q

from .models import Category, Estimate, Note, Status, Ticket
from .services import NEXT_STATUSES

TICKET_FIELDS = (
    "customer_name", "customer_contact", "customer_email", "laptop_make", "laptop_model",
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


ESTIMATE_FIELDS = ("diagnosis", "proposed_work", "total_price", "currency")


class EstimateForm(forms.ModelForm):
    draft_id = forms.IntegerField(min_value=1, max_value=2**63 - 1, required=False, widget=forms.HiddenInput)

    class Meta:
        model = Estimate
        fields = ESTIMATE_FIELDS
        widgets = {
            "diagnosis": forms.Textarea(attrs={"rows": 3}),
            "proposed_work": forms.Textarea(attrs={"rows": 3}),
        }


class SendEstimateForm(forms.Form):
    draft_id = forms.IntegerField(min_value=1, max_value=2**63 - 1, widget=forms.HiddenInput)


class DecisionForm(forms.Form):
    estimate_id = forms.IntegerField(min_value=1, max_value=2**63 - 1, widget=forms.HiddenInput)
    choice = forms.ChoiceField(choices=Estimate.Decision.choices)
