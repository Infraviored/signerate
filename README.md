# Signerate - Premium Sign Generator

Signerate is a Python-based utility for generating 3D printable signs (in 3MF or STEP format) using CadQuery.

## 🐳 Docker Deployment

This application is fully Dockerized and optimized for low resource usage.

### Running with Docker Compose
```bash
docker compose up -d --build
```

- **Port**: The application listens on port **15000** on the host.
- **Port Mapping**: `15000:15000` (internal and external match).
- **Resource Limits**: Configured with a 1024MB memory limit in `docker-compose.yml`.
- **Gunicorn**: Uses a single worker (`--workers 1`) to keep memory footprint stable.

### Volumes
- `./sets`: Stores saved sign sets as JSON files.
- `./settings.json`: Persists global application settings.

## 🛠 Features
- **Auto-seeding**: On first run, it creates a "Workshop" set if no sets exist.
- **Font Support**: Automatically detects system fonts and serves them for preview.
- **3D Export**: Supports multi-color 3MF and universal STEP output.
- **Always Ready**: The UI ensures at least one input row is always available, even if no sets are loaded.

## 💻 Local Development
If you want to run it without Docker:
1. Install requirements: `pip install -r requirements.txt`
2. Run app: `python app.py` (Defaults to port 15000)

## 🌐 NGINX Integration
When deployed behind the Infraviored NGINX proxy, authentication is handled via the `auth_type_dynamic` map. Access typically requires `?auth=SECRET` in the URL or Basic Auth credentials.
