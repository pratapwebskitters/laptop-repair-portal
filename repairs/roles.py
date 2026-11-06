def is_repair_staff(user):
    return user.is_active and user.has_perm("repairs.view_ticket") and (
        user.is_superuser or user.groups.filter(name__in=["Technicians", "Shop admins"]).exists()
    )


def can_correct(user):
    return is_repair_staff(user) and user.has_perm("repairs.correct_ticket_status")
