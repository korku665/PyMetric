# PyMetric

PyMetric is a lightweight API performance and uptime monitoring service built with **FastAPI** and **HTTPX**.

It monitors websites and APIs, measures response latency, stores check history, exposes Prometheus metrics, and provides visualization through Grafana.

## Features

- Website and API uptime monitoring
- Response latency measurement
- HTTP status code tracking
- Concurrent URL checks with FastAPI and HTTPX
- Automatic periodic monitoring
- SQLite-based monitoring history
- Prometheus metrics exporter
- Grafana monitoring dashboard
- Docker Compose support
- REST API with Swagger documentation

## Tech Stack

- Python
- FastAPI
- HTTPX
- AsyncIO
- SQLite
- Prometheus
- Grafana
- Docker
- Docker Compose

## Architecture

```text
Client
  |
  v
FastAPI API
  |
  +---- Monitor Management
  |
  +---- HTTPX Async Checker
  |
  +---- SQLite Database
  |
  +---- Prometheus Metrics
              |
              v
          Prometheus
              |
              v
            Grafana
