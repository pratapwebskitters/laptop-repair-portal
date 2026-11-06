from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.urls import reverse
from rest_framework import generics, permissions, serializers, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .forms import TICKET_FIELDS
from .models import Category, Note, Status, StatusHistory, Ticket
from .queries import find_tickets, public_link
from .roles import is_repair_staff
from .services import add_note, create_ticket, edit_ticket, rotate_link, transition


def tracking_url(request, link):
    return request.build_absolute_uri(reverse("track", args=[link.token]))


class RepairStaff(permissions.BasePermission):
    def has_permission(self, request, view):
        return is_repair_staff(request.user)


class HistorySerializer(serializers.ModelSerializer):
    actor = serializers.CharField(source="actor.get_username")

    class Meta:
        model = StatusHistory
        fields = ("previous_status", "new_status", "actor", "timestamp", "correction_reason")


class NoteSerializer(serializers.ModelSerializer):
    author = serializers.CharField(source="author.get_username", read_only=True)

    class Meta:
        model = Note
        fields = ("text", "visibility", "author", "timestamp")


class TicketSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ticket
        fields = ("id", "reference", "status", "status_version", "created_at", "updated_at") + TICKET_FIELDS
        read_only_fields = ("status", "status_version", "created_at", "updated_at")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(
            Q(active=True) | Q(pk=getattr(self.instance, "category_id", None))
        )

    def validate(self, attrs):
        if "status" in self.initial_data or "status_version" in self.initial_data:
            raise ValidationError({"status": "Use the status endpoint to change status."})
        return attrs

    def create(self, validated_data):
        return create_ticket(actor=self.context["request"].user, **validated_data)

    def update(self, instance, validated_data):
        return edit_ticket(instance, validated_data)


class TicketDetailSerializer(TicketSerializer):
    history = HistorySerializer(many=True, read_only=True)
    notes = NoteSerializer(many=True, read_only=True)
    tracking_url = serializers.SerializerMethodField()
    creator = serializers.CharField(source="creator.get_username", read_only=True)

    class Meta(TicketSerializer.Meta):
        fields = TicketSerializer.Meta.fields + ("history", "notes", "tracking_url", "creator")

    def get_tracking_url(self, ticket):
        return tracking_url(self.context["request"], ticket.tracking_links.get(revoked_at__isnull=True))


class TransitionSerializer(serializers.Serializer):
    expected_status = serializers.ChoiceField(choices=Status.choices)
    expected_version = serializers.IntegerField(min_value=0, max_value=2**63 - 1)
    new_status = serializers.ChoiceField(choices=Status.choices)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class PublicUpdateSerializer(serializers.Serializer):
    text = serializers.CharField()
    timestamp = serializers.DateTimeField()


class PublicTicketSerializer(serializers.Serializer):
    reference = serializers.CharField()
    status = serializers.CharField()
    status_label = serializers.CharField(source="get_status_display")
    estimated_pickup = serializers.DateField()
    updates = serializers.SerializerMethodField()

    def get_updates(self, ticket):
        notes = ticket.notes.filter(visibility=Note.Visibility.CUSTOMER)
        return PublicUpdateSerializer(notes, many=True).data


class TicketList(generics.ListCreateAPIView):
    permission_classes = [RepairStaff]
    serializer_class = TicketSerializer

    def get_queryset(self):
        return find_tickets(self.request.query_params)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ticket = serializer.save()
        detail = TicketDetailSerializer(ticket, context=self.get_serializer_context())
        return Response(detail.data, status=status.HTTP_201_CREATED)


class TicketDetail(generics.RetrieveUpdateAPIView):
    permission_classes = [RepairStaff]
    serializer_class = TicketDetailSerializer
    queryset = Ticket.objects.all()
    http_method_names = ["get", "patch", "head", "options"]


class TicketAction(generics.GenericAPIView):
    permission_classes = [RepairStaff]
    queryset = Ticket.objects.all()


class ChangeStatus(TicketAction):
    serializer_class = TransitionSerializer

    def post(self, request, pk):
        ticket = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            transition(ticket_id=ticket.pk, actor=request.user, **serializer.validated_data)
        except DjangoValidationError as error:
            raise ValidationError({"status": error.messages}) from error
        ticket.refresh_from_db()
        return Response(TicketDetailSerializer(ticket, context=self.get_serializer_context()).data)


class CreateNote(TicketAction):
    serializer_class = NoteSerializer

    def post(self, request, pk):
        ticket = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        note = add_note(ticket, request.user, **serializer.validated_data)
        return Response(self.get_serializer(note).data, status=status.HTTP_201_CREATED)


class RotateLink(TicketAction):
    def post(self, request, pk):
        link = rotate_link(self.get_object())
        return Response({"tracking_url": tracking_url(request, link)})


class PublicTracking(APIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def get(self, request, token):
        link = public_link(token)
        if link is None:
            raise NotFound("Tracking link not found.")
        return Response(PublicTicketSerializer(link.ticket).data)
