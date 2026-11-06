# Laptop Repair Status Portal

A single-shop Django application for staff to manage laptop repairs and share private tracking links with customers. Staff use the dashboard or the session-authenticated REST API. Customers can open their link without an account.

## Local setup

Use Python 3.10 or newer. The default database is SQLite.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

Open [the staff portal](http://127.0.0.1:8000/) and sign in as `demo-tech` or `demo-admin`. Both use `RepairDemo!2026` on a fresh local database. The demo command can be run again without duplicating the sample tickets or resetting existing accounts. It requires debug mode.

To initialize a real shop, run the migrations and `python manage.py createsuperuser`. Sign in at `/admin/` and create at least one active category. Add staff accounts with **Staff status** and the appropriate group. A **Shop admins** member can subsequently create or deactivate staff accounts, manage categories, and correct ticket status. The user and category screens are in Django Admin. Deactivate categories to keep them available on existing tickets and remove them from new intake choices.

## Try the demo

1. Sign in as `demo-tech`. Find the ready and overdue sample tickets on the dashboard. Search by a full reference or any part of a customer name.
2. Create a ticket, set an estimated pickup date, and use **Copy link** on its detail page. Open the link in a private window at a phone-sized viewport.
3. Move the ticket through Diagnosing, Repairing, Ready for pickup, and Picked up. Refresh the public page after each change. The staff history records the actor and time. Diagnosing can also move directly to Ready for pickup. Cancellation is available until pickup.
4. Add an internal note and a customer-visible update. Check the public web and API responses. Regenerate the link, then open the old and new URLs.
5. Sign in as `demo-admin` to record a correction with a reason from ticket detail or from **Correct status** on the Django Admin ticket page.

Run the automated acceptance suite and check for uncommitted model changes with these commands.

```sh
python manage.py test
python manage.py check
python manage.py makemigrations --check --dry-run
```

## REST API

Staff authenticate via `/login/`. A client must retain the Django session cookie and send the CSRF cookie value as `X-CSRFToken` with authenticated writes. Use `Content-Type: application/json` for JSON bodies. For same-origin browser code, sign in normally and read the CSRF cookie after visiting a staff form. Successful list responses include `count`, `next`, `previous`, and `results`.

| Method | Path | Input and output |
| --- | --- | --- |
| GET | `/api/v1/tickets/` | Query parameters `q`, `status`, `category` (ID), and `page`. Returns paginated tickets. |
| POST | `/api/v1/tickets/` | Returns 201 with ticket details, history, and the active `tracking_url`. |
| GET | `/api/v1/tickets/{id}/` | Returns ticket details, history, notes, and the active tracking URL. |
| PATCH | `/api/v1/tickets/{id}/` | Send editable intake fields. Use `null` to clear the pickup estimate. |
| POST | `/api/v1/tickets/{id}/status/` | Send the expected status and version from the last read, the new status, and a reason for admin corrections. Returns updated details. |
| POST | `/api/v1/tickets/{id}/notes/` | Send `text` and optional `visibility` (`internal` by default, or `customer`). Returns 201 and the note. |
| POST | `/api/v1/tickets/{id}/tracking-link/` | Returns a replacement `tracking_url`. |
| GET | `/api/v1/track/{token}/` | Public status and customer-visible updates. Invalid and revoked tokens return the same 404. |

Example intake body, using an active category ID.

```json
{
  "customer_name": "Priya Rao",
  "customer_contact": "priya@example.test",
  "laptop_make": "Lenovo",
  "laptop_model": "ThinkPad T14",
  "reported_issue": "Battery will not charge",
  "category": 1,
  "estimated_pickup": "2026-11-20"
}
```

Example status request for a newly created ticket.

```json
{"expected_status": "received", "expected_version": 0, "new_status": "diagnosing"}
```

A stale request returns 400 with a message such as `{"status":["The status has changed. Refresh the ticket and try again."]}`. Re-read the detail before deciding whether to submit a new transition. Missing required inputs and invalid choices also return 400. Staff requests without the required session, permissions, or CSRF token return 403. Inactive categories can be retained on existing tickets.

The public API returns an explicit allowlist.

```json
{
  "reference": "REP-000003",
  "status": "diagnosing",
  "status_label": "Diagnosing",
  "estimated_pickup": "2026-11-20",
  "updates": [
    {"text": "We are checking the charging port.", "timestamp": "2026-11-06T12:30:00+05:30"}
  ]
}
```

## Staging and production deployment

See the [deployment runbook](DEPLOYMENT.md) for configuration, release commands, and PostgreSQL backup and restore.
