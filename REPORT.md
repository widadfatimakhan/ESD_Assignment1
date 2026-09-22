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
> **TODO:** Filebeat → Elasticsearch → Kibana. What we log, why, and where in
> the code; how Filebeat parses JSON into fields; where logs live and retention;
> a sample log with its stored fields and a working Kibana search.

## Part D — System design
> **TODO:** architecture diagram (app + Prometheus + Grafana + logging stack,
> with arrows, data stores, and failure notes); and a walk-through of one metric
> and one log end-to-end.

## Part E — Experiments
> **TODO:** (1) inject a delay into the availability check and show before/during/
> after on the p95 panel; (2) cardinality experiment with a `request_id`-style
> label, showing series growth then removal.

---