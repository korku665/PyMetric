import asyncio
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, HttpUrl, Field
from prometheus_client import Gauge, Counter, generate_latest, CONTENT_TYPE_LATEST

DB_PATH = Path(os.getenv('PYMETRIC_DB_PATH', 'pymetric.db'))
CHECK_INTERVAL_SECONDS = 60
REQUEST_TIMEOUT_SECONDS = 10

# Prometheus metrics
UP = Gauge('pymetric_target_up', 'Whether target is up (1) or down (0)', ['monitor_id', 'name', 'url'])
LATENCY = Gauge('pymetric_latency_ms', 'Last request latency in milliseconds', ['monitor_id', 'name', 'url'])
STATUS_CODE = Gauge('pymetric_status_code', 'Last HTTP status code', ['monitor_id', 'name', 'url'])
CHECKS_TOTAL = Counter('pymetric_checks_total', 'Total monitor checks', ['monitor_id', 'name', 'url', 'result'])


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS monitors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                interval_seconds INTEGER NOT NULL DEFAULT 60,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                monitor_id INTEGER NOT NULL,
                checked_at TEXT NOT NULL,
                is_up INTEGER NOT NULL,
                status_code INTEGER,
                latency_ms REAL,
                error TEXT,
                FOREIGN KEY (monitor_id) REFERENCES monitors(id) ON DELETE CASCADE
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_checks_monitor_id ON checks(monitor_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_checks_checked_at ON checks(checked_at)')
        conn.commit()


class MonitorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: HttpUrl
    interval_seconds: int = Field(default=60, ge=10, le=3600)


class MonitorUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    url: Optional[HttpUrl] = None
    interval_seconds: Optional[int] = Field(default=None, ge=10, le=3600)
    enabled: Optional[bool] = None


async def perform_check(monitor: sqlite3.Row | dict) -> dict:
    monitor_id = str(monitor['id'])
    name = monitor['name']
    url = monitor['url']
    started = time.perf_counter()
    status_code = None
    error = None
    is_up = False

    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={'User-Agent': 'PyMetric/1.0'}
        ) as client:
            response = await client.get(url)
            status_code = response.status_code
            is_up = 200 <= response.status_code < 500
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'

    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    checked_at = utc_now()

    with db() as conn:
        conn.execute(
            '''INSERT INTO checks (monitor_id, checked_at, is_up, status_code, latency_ms, error)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (monitor['id'], checked_at, int(is_up), status_code, latency_ms, error),
        )
        conn.commit()

    labels = {'monitor_id': monitor_id, 'name': name, 'url': url}
    UP.labels(**labels).set(1 if is_up else 0)
    LATENCY.labels(**labels).set(latency_ms)
    STATUS_CODE.labels(**labels).set(status_code or 0)
    CHECKS_TOTAL.labels(**labels, result='up' if is_up else 'down').inc()

    return {
        'monitor_id': monitor['id'],
        'name': name,
        'url': url,
        'checked_at': checked_at,
        'is_up': is_up,
        'status_code': status_code,
        'latency_ms': latency_ms,
        'error': error,
    }


async def scheduler_loop() -> None:
    last_run: dict[int, float] = {}
    while True:
        try:
            now = time.monotonic()
            with db() as conn:
                monitors = conn.execute('SELECT * FROM monitors WHERE enabled = 1').fetchall()

            due = []
            for monitor in monitors:
                interval = monitor['interval_seconds']
                if now - last_run.get(monitor['id'], 0) >= interval:
                    due.append(monitor)
                    last_run[monitor['id']] = now

            if due:
                await asyncio.gather(*(perform_check(m) for m in due), return_exceptions=True)
        except Exception as exc:
            print('Scheduler error:', exc)

        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(scheduler_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title='PyMetric API Performance & Uptime Monitor',
    version='1.0.0',
    description='Simple uptime + latency monitoring backend with Prometheus/Grafana integration.',
    lifespan=lifespan,
)


@app.get('/')
def root():
    return {
        'name': 'PyMetric',
        'status': 'running',
        'docs': '/docs',
        'metrics': '/metrics',
    }


@app.get('/health')
def health():
    return {'ok': True, 'time': utc_now()}


@app.post('/monitors', status_code=201)
def create_monitor(payload: MonitorCreate):
    with db() as conn:
        cur = conn.execute(
            'INSERT INTO monitors (name, url, interval_seconds, enabled, created_at) VALUES (?, ?, ?, 1, ?)',
            (payload.name, str(payload.url), payload.interval_seconds, utc_now()),
        )
        conn.commit()
        monitor_id = cur.lastrowid
        row = conn.execute('SELECT * FROM monitors WHERE id = ?', (monitor_id,)).fetchone()
    return dict(row)


@app.get('/monitors')
def list_monitors():
    with db() as conn:
        monitors = conn.execute('SELECT * FROM monitors ORDER BY id DESC').fetchall()
        result = []
        for monitor in monitors:
            last = conn.execute(
                'SELECT * FROM checks WHERE monitor_id = ? ORDER BY id DESC LIMIT 1',
                (monitor['id'],),
            ).fetchone()
            item = dict(monitor)
            item['enabled'] = bool(item['enabled'])
            item['last_check'] = dict(last) if last else None
            if item['last_check']:
                item['last_check']['is_up'] = bool(item['last_check']['is_up'])
            result.append(item)
    return result


@app.get('/monitors/{monitor_id}')
def get_monitor(monitor_id: int):
    with db() as conn:
        monitor = conn.execute('SELECT * FROM monitors WHERE id = ?', (monitor_id,)).fetchone()
        if not monitor:
            raise HTTPException(404, 'Monitor not found')
        data = dict(monitor)
        data['enabled'] = bool(data['enabled'])
        return data


@app.patch('/monitors/{monitor_id}')
def update_monitor(monitor_id: int, payload: MonitorUpdate):
    changes = payload.model_dump(exclude_unset=True)
    if 'url' in changes and changes['url'] is not None:
        changes['url'] = str(changes['url'])
    if 'enabled' in changes:
        changes['enabled'] = int(changes['enabled'])
    if not changes:
        return get_monitor(monitor_id)

    with db() as conn:
        exists = conn.execute('SELECT id FROM monitors WHERE id = ?', (monitor_id,)).fetchone()
        if not exists:
            raise HTTPException(404, 'Monitor not found')
        set_clause = ', '.join(f'{key} = ?' for key in changes)
        conn.execute(f'UPDATE monitors SET {set_clause} WHERE id = ?', (*changes.values(), monitor_id))
        conn.commit()
    return get_monitor(monitor_id)


@app.delete('/monitors/{monitor_id}', status_code=204)
def delete_monitor(monitor_id: int):
    with db() as conn:
        exists = conn.execute('SELECT id FROM monitors WHERE id = ?', (monitor_id,)).fetchone()
        if not exists:
            raise HTTPException(404, 'Monitor not found')
        conn.execute('DELETE FROM checks WHERE monitor_id = ?', (monitor_id,))
        conn.execute('DELETE FROM monitors WHERE id = ?', (monitor_id,))
        conn.commit()
    return Response(status_code=204)


@app.post('/monitors/{monitor_id}/check')
async def check_now(monitor_id: int):
    with db() as conn:
        monitor = conn.execute('SELECT * FROM monitors WHERE id = ?', (monitor_id,)).fetchone()
        if not monitor:
            raise HTTPException(404, 'Monitor not found')
    return await perform_check(monitor)


@app.post('/check-all')
async def check_all():
    with db() as conn:
        monitors = conn.execute('SELECT * FROM monitors WHERE enabled = 1').fetchall()
    if not monitors:
        return []
    return await asyncio.gather(*(perform_check(m) for m in monitors))


@app.get('/monitors/{monitor_id}/history')
def monitor_history(
    monitor_id: int,
    limit: int = Query(default=100, ge=1, le=1000),
):
    with db() as conn:
        exists = conn.execute('SELECT id FROM monitors WHERE id = ?', (monitor_id,)).fetchone()
        if not exists:
            raise HTTPException(404, 'Monitor not found')
        rows = conn.execute(
            'SELECT * FROM checks WHERE monitor_id = ? ORDER BY id DESC LIMIT ?',
            (monitor_id, limit),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item['is_up'] = bool(item['is_up'])
            result.append(item)
    return result


@app.get('/stats')
def stats():
    with db() as conn:
        total_monitors = conn.execute('SELECT COUNT(*) AS c FROM monitors').fetchone()['c']
        enabled_monitors = conn.execute('SELECT COUNT(*) AS c FROM monitors WHERE enabled = 1').fetchone()['c']
        total_checks = conn.execute('SELECT COUNT(*) AS c FROM checks').fetchone()['c']
        up_checks = conn.execute('SELECT COUNT(*) AS c FROM checks WHERE is_up = 1').fetchone()['c']
        avg_latency = conn.execute('SELECT AVG(latency_ms) AS v FROM checks').fetchone()['v']
    uptime_percent = round((up_checks / total_checks) * 100, 2) if total_checks else None
    return {
        'total_monitors': total_monitors,
        'enabled_monitors': enabled_monitors,
        'total_checks': total_checks,
        'uptime_percent': uptime_percent,
        'average_latency_ms': round(avg_latency, 2) if avg_latency is not None else None,
    }


@app.get('/metrics')
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
