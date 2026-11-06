# Staging deployment

Run Django under Gunicorn behind an HTTPS reverse proxy. Use a dedicated PostgreSQL database for staging. Give the service account read access to the release and the proxy read access to the collected static files.

## Configuration

Set these variables in the hosting platform or service manager. Pass the same environment to the release commands and the Gunicorn service. Django reads the process environment directly.

| Variable | Staging value |
| --- | --- |
| `DJANGO_DEBUG` | `0`. Local development defaults to `1`. |
| `DJANGO_SECRET_KEY` | Required. Generate a unique key with `python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'` and store it in the secret manager. Keep it stable across releases. |
| `DJANGO_ALLOWED_HOSTS` | Required. Comma-separated hostnames, such as `repairs-staging.example.com`. Use hostnames without a scheme or port. |
| `DJANGO_TRUST_HTTPS_PROXY` | `1` when your trusted proxy terminates HTTPS and sets `X-Forwarded-Proto`. The proxy must overwrite any client-supplied value. |
| `SHOP_TIME_ZONE` | Shop IANA time zone. Defaults to `Asia/Kolkata`. |
| `POSTGRES_DB` | Required database name, such as `repair_portal_staging`. |
| `POSTGRES_USER`, `POSTGRES_PASSWORD` | Required credentials for the dedicated database role. Store the password in the secret manager. |
| `POSTGRES_HOST`, `POSTGRES_PORT` | Database endpoint and port. Default to `localhost` and `5432`. Use the certificate hostname for remote TLS. |
| `POSTGRES_SSLMODE` | Defaults to `verify-full` with debug disabled. |
| `PGSSLROOTCERT` | Absolute path to the database provider's trusted CA certificate when needed. Set this for both the app and PostgreSQL client commands. |

Use the same public HTTPS origin for staff pages and API writes so Django's session CSRF checks work. Have the proxy preserve the public `Host` header and forward `X-Forwarded-Proto: https`. Keep Gunicorn's listener private.

## Provision PostgreSQL

Create a dedicated database and login using the provider console, or run the following in `psql` as a database administrator. The password command prompts interactively.

```sql
CREATE ROLE repair_portal_staging LOGIN;
\password repair_portal_staging
CREATE DATABASE repair_portal_staging OWNER repair_portal_staging;
```

The release role needs to create and alter tables and indexes in this database. Restrict database network access to the staging application and authorized operators. Install PostgreSQL client tools compatible with the server version.

## Release

Use the project's virtual environment. Run migrations once per release before routing traffic to the new application workers. Take a database backup before applying migrations to an existing environment.

```sh
python -m pip install -r requirements-production.txt
python manage.py check --deploy
python manage.py migrate --plan
python manage.py migrate --noinput
python manage.py collectstatic --noinput
python manage.py createsuperuser  # First deployment only
gunicorn portal.wsgi:application --bind 127.0.0.1:8000 --workers 2
```

Run Gunicorn under a service manager. Configure the HTTPS proxy to serve `/static/` from the release's `staticfiles/` directory and forward other paths to Gunicorn. Redirect HTTP to HTTPS. Django sets HSTS for the staging hostname. Deployment checks may report `security.W005` and `security.W021` because subdomain HSTS and browser preload require a separate domain-wide decision.

Verify the login page and its stylesheet over HTTPS. Sign in to Django Admin, add a category and a test staff account, then create a synthetic ticket and verify the HTTPS tracking link in a signed-out browser. Confirm the proxy and monitoring redact tracking URLs, including `/track/` and `/api/v1/track/`. These URLs grant access to a repair. Use a restricted staging environment for any real customer data.

## Backup and restore

Export the PostgreSQL connection settings from the deployment configuration. Supply credentials through an approved secret mechanism such as a mode-0600 `PGPASSFILE`.

```sh
export PGHOST="$POSTGRES_HOST" PGPORT="${POSTGRES_PORT:-5432}"
export PGUSER="$POSTGRES_USER" PGSSLMODE="${POSTGRES_SSLMODE:-verify-full}"
umask 077
backup="repair-portal-$(date -u +%Y%m%dT%H%M%SZ).dump"
pg_dump --format=custom --no-owner --no-acl --dbname="$POSTGRES_DB" --file="$backup"
pg_restore --list "$backup"
```

Store backups encrypted with restricted access and configure retention. Restore into a newly provisioned, empty database owned by the restore role. Keep it isolated from the active app while testing the restore.

```sh
restore_db="repair_portal_restore_check"
test "$restore_db" != "$POSTGRES_DB" &&
  pg_restore --exit-on-error --single-transaction --no-owner --no-acl \
    --dbname="$restore_db" "$backup" &&
  POSTGRES_DB="$restore_db" python manage.py migrate --check
```

For the restore drill, point an isolated app instance at the restored database and verify login, ticket counts, and history. For recovery, confirm the restored data and the matching application release before switching the service's database connection. Keep the previous database available according to the recovery plan.

## Deployment inputs

- Staging host or platform, deployment access, and the public hostname with DNS and TLS configured.
- PostgreSQL endpoint, database and role names, a password supplied through the secret manager, network access, and the provider CA when applicable.
- An app secret, the initial administrator username, the shop time zone, and a private way to set the administrator password.
- Backup storage and retention policy, plus a separate database for restore drills.

## Neon PostgreSQL

The app uses Django's PostgreSQL backend with psycopg. When set, `DATABASE_URL` selects PostgreSQL and takes precedence over the individual `POSTGRES_*` settings. Set the pooled `DATABASE_URL` in the web service environment. Use `DATABASE_URL_UNPOOLED` for migrations and database tools. Keep both URLs private.

For local verification, load the file written by the Neon CLI using Node's dotenv support. Substitute `.env` if the CLI wrote there. The commands below assume the Python virtual environment is active and the production requirements are installed.

```sh
npm run build
node --env-file=.env.local -e 'const {execFileSync}=require("node:child_process"); const url=process.env.DATABASE_URL_UNPOOLED; if (!url) throw Error("Missing direct database URL"); execFileSync("python", ["manage.py","migrate","--noinput"], {stdio:"inherit",env:{...process.env,DATABASE_URL:url}})'
node --env-file=.env.local -e 'require("node:child_process").execFileSync("python", ["manage.py","runserver"], {stdio:"inherit"})'
```

For backups on Neon, pass the direct URL as the PostgreSQL connection string using your secret manager. Use a separate direct connection to an empty target for restores.
