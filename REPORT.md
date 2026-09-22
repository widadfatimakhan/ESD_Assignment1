# Enterprise Software Development — Assignment 1: Observability
**StudySlot: a campus study-room booking service**
Widad Fatima Khan · Habib University · Fall 2026

---

## Part A — The project

### Problem
In the campus library, group study rooms are booked by physically asking a
librarian, who then walks over to check whether a room is free. If none are
free, groups simply wait. This is slow for students and tedious for staff.

### Intended users
- **Students** — book a group room for a 30- or 60-minute slot in advance,
  listing the student IDs of everyone in the group.
- **Librarians** — see at a glance which rooms are occupied at any moment,
  without walking the floors.

### Solution
A small web service (Python + FastAPI). A student requests a room for a slot
and supplies the group's student IDs. The service checks that the room+slot is
free and that the group fits the room's size limits, books it if so, and
rejects it otherwise — so there is no double-booking. Bookings can be cancelled,
which frees the room. The service exposes live metrics (bookings made, rooms
occupied, availability-check time) and structured logs, which are collected and
visualised by the monitoring stack described below.

### Rooms and rules
| Room | Capacity (max) | Min group |
|------|----------------|-----------|
| R1 | 20 | 10 |
| R2 | 12 | 6  |
| R3 | 10 | 5  |
| R4 | 8  | 3  |
| R5 | 8  | 3  |

- A `(room, slot)` pair can be booked only once → prevents double-booking.
- A group larger than a room's capacity, or smaller than its minimum, is
  rejected with a machine-readable reason (`group_too_large`,
  `group_too_small`, `double_booked`) and a list of suggested rooms that fit.

### How to try it
> **TODO (fill at the end):** exact run commands. For now: activate the venv,
> `cd studyslot`, `uvicorn main:app --reload`, open `http://127.0.0.1:8000/docs`,
> and use the `POST /book`, `GET /availability`, `GET /bookings`, and
> `DELETE /bookings/{room}/{slot}` endpoints.

---

## Part B — Metrics and dashboards

### Stack
- The app exposes a `/metrics` endpoint using the `prometheus-client` library.
- **Prometheus** (in Docker) scrapes `/metrics` every 5 seconds and stores the
  values as time series.
- **Grafana** (in Docker) reads from Prometheus and draws live dashboards. The
  Prometheus data source is auto-provisioned on startup.
> **TODO:** add Node Exporter (machine CPU/memory/disk/network) — not yet done.

### The four metric types

| Metric name | Type | Purpose | Unit | Labels | Where recorded in code |
|-------------|------|---------|------|--------|------------------------|
| `studyslot_bookings_total` | Counter | Count successful bookings (only ever rises) | bookings | none | `book()`, on success: `BOOKINGS_TOTAL.inc()` |
| `studyslot_rooms_occupied` | Gauge | Room-slots booked right now (rises on book, falls on cancel) | room-slots | none | `book()`: `.inc()`; `cancel()`: `.dec()` |
| `studyslot_availability_check_seconds` | Histogram | Time for the availability check, bucketed for p95/p99 | seconds | none (buckets) | `book()`, `record_check_time()`: `.observe(elapsed)` |
| `studyslot_availability_check_summary_seconds` | Summary | Same timing as average (`_sum`/`_count`); Python summaries have no percentiles | seconds | none | `book()`, `record_check_time()`: `.observe(elapsed)` |

> **TODO:** add the self-explored / business metrics (e.g. rejections by reason)
> once we wire them up, and confirm all four types appear across the project.

### Dashboard panels (PromQL)

| Panel | Query | What it shows |
|-------|-------|---------------|
| Booking rate | `rate(studyslot_bookings_total[1m])` | bookings per second, recent |
| Rooms occupied now | `studyslot_rooms_occupied` | current occupancy (Stat panel) |
| Availability p95 | `histogram_quantile(0.95, rate(studyslot_availability_check_seconds_bucket[5m]))` | 95th-percentile check time; flat near-zero at baseline |
| Availability average | `rate(...summary_seconds_sum[5m]) / rate(...summary_seconds_count[5m])` | mean check time, recent |

**On percentiles and the time window.** The histogram exposes cumulative
`_bucket{le="..."}` counts; `histogram_quantile(0.95, rate(...[5m]))` estimates
the 95th percentile over a 5-minute sliding window — i.e. "95% of availability
checks in the last 5 minutes were faster than this value." The Summary cannot
produce percentiles in the Python client, so it is reported as an average
(`_sum / _count`); the histogram provides p95/p99.

> **TODO:** insert Grafana screenshots (all panels), and one screenshot of the
> Prometheus `/targets` page showing the target UP.

---

## Part C — Logging pipeline

### Pipeline overview
StudySlot's logs travel through this path:

App writes one JSON line to `studyslot/logs/app.log`
→ Filebeat (in Docker) reads and parses that file
→ Elasticsearch (in Docker) stores each line as a searchable document
→ Kibana (in Docker) is used to search and inspect them.

### What we log, why, and where in the code
Every booking attempt and cancellation writes exactly one structured JSON
event via the `log_event()` helper in `studyslot/main.py`. We log at the
decision points inside `book()` (each rejection branch and the success path)
and in `cancel()`. This captures *what happened to each request* without
logging noise on every unrelated call.

Each event carries: `@timestamp` (UTC, ISO-8601), `service.name`, `log.level`
(`info` for success/cancel, `warning` for rejections), `message`, a per-request
`request_id` (an 8-char ID generated at the top of `book()`), and context
fields `room`, `slot`, `group_size`, `status`, and `reason` (for rejections:
`group_too_large`, `group_too_small`, `double_booked`, etc.). We never log
student IDs beyond counting them, and no secrets or personal data are logged.

### How Filebeat collects and parses the logs
Filebeat (`monitoring/filebeat/filebeat.yml`) watches `/logs/app.log` (the
app's log folder is mounted into the Filebeat container read-only at `/logs`).
Its `filestream` input tails the file for new lines. The `ndjson` parser reads
each line as a JSON object and lifts every key to the top level
(`target: ""`), so `room`, `status`, `request_id`, etc. become individual
searchable fields in Elasticsearch rather than one opaque text blob. Filebeat
also adds its own metadata (`agent.*`, `host.name`, `log.file.path`).

### Where logs live, what survives restarts, retention
Logs are written to `studyslot/logs/app.log` on disk (survives everything) and
shipped into Elasticsearch under daily indices named `studyslot-logs-YYYY.MM.DD`.
Elasticsearch currently stores its data inside the container (no named volume
yet), so `docker compose down` would clear the indexed copy — the on-disk
`app.log` is the durable source of truth. (A named volume will be added in the
Dockerize step to make the index persistent.) No automatic deletion (ILM) is
configured; for this classroom scale, old daily indices can be deleted manually.

### Searching in Kibana
A data view `StudySlot Logs` over pattern `studyslot-logs-*` (timestamp field
`@timestamp`) is used in **Discover**. Example searches (KQL):
- `reason : "group_too_small"` — all rejections of that kind (see screenshot).
- `status : 200` — all confirmed bookings.
- `request_id : "<id>"` — trace one specific request end to end.
- `request_id : a*` — wildcard/pattern match: every request whose ID begins
  with `a`.

A sample stored event (fields after Filebeat parsing):
`@timestamp`, `service.name=studyslot`, `log.level=warning`,
`message="booking rejected"`, `request_id`, `room`, `slot`, `group_size`,
`status=400`, `reason=group_too_small`, plus Filebeat's `agent.*`,
`host.name`, `log.file.path=/logs/app.log`.

> Screenshots: Kibana Discover with an expanded event showing parsed fields;
> the `reason : "group_too_small"` search returning one document.

## Part D — System design
> **TODO:** architecture diagram (app + Prometheus + Grafana + logging stack,
> with arrows, data stores, and failure notes); and a walk-through of one metric
> and one log end-to-end.

## Part E — Experiments
> **TODO:** (1) inject a delay into the availability check and show before/during/
> after on the p95 panel; (2) cardinality experiment with a `request_id`-style
> label, showing series growth then removal.

---