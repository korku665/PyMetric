# PyMetric — API Performance & Uptime Monitor

Basit ama çalışan bir uptime/latency monitor backend projesi.

## Özellikler

- FastAPI REST API
- HTTPX ile async web sitesi kontrolleri
- Birden fazla siteyi aynı anda kontrol etme
- SQLite üzerinde monitor ve geçmiş kayıtları
- Otomatik periyodik kontrol
- Prometheus `/metrics` endpoint'i
- Grafana datasource + hazır dashboard
- Docker Compose ile tek komutta ayağa kalkar

## En hızlı çalıştırma

Docker Desktop kuruluysa proje klasöründe:

```bash
docker compose up --build
```

Sonra:

- FastAPI Swagger: http://localhost:8000/docs
- API: http://localhost:8000
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000
- Grafana kullanıcı: `admin`
- Grafana şifre: `admin`

## Monitor ekleme

Swagger üzerinden `POST /monitors` çağırabilir veya:

```bash
curl -X POST "http://localhost:8000/monitors" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Google",
    "url": "https://www.google.com",
    "interval_seconds": 30
  }'
```

## Tüm monitorleri kontrol et

```bash
curl -X POST http://localhost:8000/check-all
```

## Geçmiş

```bash
curl http://localhost:8000/monitors/1/history
```

## İstatistik

```bash
curl http://localhost:8000/stats
```

## Docker olmadan çalıştırma

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Ardından:

```powershell
uvicorn app.main:app --reload
```

## API endpointleri

- `GET /health`
- `POST /monitors`
- `GET /monitors`
- `GET /monitors/{id}`
- `PATCH /monitors/{id}`
- `DELETE /monitors/{id}`
- `POST /monitors/{id}/check`
- `POST /check-all`
- `GET /monitors/{id}/history`
- `GET /stats`
- `GET /metrics`
