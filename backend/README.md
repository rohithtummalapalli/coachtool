# Backend Admin Service

This folder contains a production-oriented Django backend for:
- User management
- Group and permission management
- Organization and membership roles (owner/admin/member/viewer)
- Admin panel and REST API

## Setup

From repository root:

```powershell
uv sync --python 3.12
```

Create backend env:

```powershell
Use the root `.env` file only (project root).
```

Run migrations and create admin:

```powershell
cd backend
$env:DJANGO_SETTINGS_MODULE="config.settings"
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

## URLs

- Admin: `http://127.0.0.1:8000/admin/`
- API docs: `http://127.0.0.1:8000/api/docs/`
- API root: `http://127.0.0.1:8000/api/accounts/`
