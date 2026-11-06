from django.db import migrations


def create_roles(apps, schema_editor):
    db = schema_editor.connection.alias
    groups = apps.get_model("auth", "Group").objects.using(db)
    permissions = apps.get_model("auth", "Permission").objects.using(db)
    content_types = apps.get_model("contenttypes", "ContentType").objects.using(db)
    staff = [
        ("repairs", "ticket", "view_ticket", "Can view ticket"),
        ("repairs", "statushistory", "view_statushistory", "Can view status history"),
    ]
    admin = staff + [
        ("repairs", "ticket", "correct_ticket_status", "Can correct ticket status"),
        ("repairs", "category", "view_category", "Can view category"),
        ("repairs", "category", "add_category", "Can add category"),
        ("repairs", "category", "change_category", "Can change category"),
        ("auth", "user", "view_user", "Can view user"),
        ("auth", "user", "add_user", "Can add user"),
        ("auth", "user", "change_user", "Can change user"),
    ]
    for name, entries in [("Technicians", staff), ("Shop admins", admin)]:
        group, _ = groups.get_or_create(name=name)
        for app_label, model, codename, label in entries:
            ct, _ = content_types.get_or_create(app_label=app_label, model=model)
            permission, _ = permissions.get_or_create(
                content_type=ct, codename=codename, defaults={"name": label}
            )
            group.permissions.add(permission)


class Migration(migrations.Migration):
    dependencies = [("repairs", "0001_initial"), ("auth", "0012_alter_user_first_name_max_length")]
    operations = [migrations.RunPython(create_roles, migrations.RunPython.noop)]
