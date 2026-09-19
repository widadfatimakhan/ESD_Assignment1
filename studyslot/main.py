from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from prometheus_client import (
    Counter, Gauge, Histogram, Summary,
    generate_latest, CONTENT_TYPE_LATEST,
)
import time

app = FastAPI(title="StudySlot")
# --- Metrics ---
# A Counter only ever goes up. This one counts successful bookings.
BOOKINGS_TOTAL = Counter(
    "studyslot_bookings_total",
    "Total number of successful room bookings",
)
# A Gauge goes up AND down. Rooms occupied right now.
ROOMS_OCCUPIED = Gauge(
    "studyslot_rooms_occupied",
    "Number of room-slots currently booked",
)

# A Histogram times an operation and sorts each timing into buckets.
# Buckets are in seconds; these match the ranges your assignment mentions.
AVAILABILITY_CHECK_SECONDS = Histogram(
    "studyslot_availability_check_seconds",
    "Time spent checking room availability during a booking",
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
)

# A Summary also times an operation, but reports a running average
# (Python summaries don't do percentiles — the histogram covers those).
AVAILABILITY_CHECK_SUMMARY = Summary(
    "studyslot_availability_check_summary_seconds",
    "Summary of availability-check duration (average via _sum / _count)",
)

# --- Rooms, each with a capacity and a minimum group size ---
# capacity  = the most students the room fits
# min_group = the fewest students allowed to book it (stops a big room
#             being hogged by one or two people)
ROOMS = {
    "R1": {"capacity": 20, "min_group": 10},
    "R2": {"capacity": 12, "min_group": 6},
    "R3": {"capacity": 10,  "min_group": 5},
    "R4": {"capacity": 8,  "min_group": 3},
    "R5": {"capacity": 8,  "min_group": 3},
}

SLOTS = ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30",
         "12:00", "12:30", "13:00", "13:30", "14:00", "14:30",
         "15:00", "15:30", "16:00", "16:30"]

# (room, slot) -> list of student IDs. A pair can appear once => no double-booking.
bookings: dict[tuple[str, str], list[str]] = {}


class BookingRequest(BaseModel):
    room: str
    slot: str
    student_ids: list[str]


def rooms_that_fit(group_size: int, slot: str) -> list[str]:
    """Rooms whose size range fits this group AND are free for this slot."""
    return [
        room for room, info in ROOMS.items()
        if info["min_group"] <= group_size <= info["capacity"]
        and (room, slot) not in bookings
    ]


@app.get("/")
def home():
    return {"service": "StudySlot", "status": "running"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/rooms")
def list_rooms():
    """Every room with its size limits and how many slots are free."""
    result = []
    for room, info in ROOMS.items():
        booked_slots = [slot for (r, slot) in bookings if r == room]
        result.append({
            "room": room,
            "capacity": info["capacity"],
            "min_group": info["min_group"],
            "booked_slots": sorted(booked_slots),
            "free_slot_count": len(SLOTS) - len(booked_slots),
        })
    return result


@app.get("/availability")
def availability(slot: str, group_size: int | None = None):
    """Free rooms for a slot. Pass group_size to see only rooms that fit."""
    if slot not in SLOTS:
        raise HTTPException(status_code=400, detail=f"Unknown slot '{slot}'")
    free = [room for room in ROOMS if (room, slot) not in bookings]
    if group_size is not None:
        free = [
            room for room in free
            if ROOMS[room]["min_group"] <= group_size <= ROOMS[room]["capacity"]
        ]
    return {
        "slot": slot,
        "group_size": group_size,
        "free_rooms": [
            {"room": r, "capacity": ROOMS[r]["capacity"], "min_group": ROOMS[r]["min_group"]}
            for r in free
        ],
    }


@app.post("/book")
def book(req: BookingRequest):
    """Book a room for a slot for a group. Enforces size limits and no double-booking."""
    if req.room not in ROOMS:
        raise HTTPException(status_code=400, detail=f"Unknown room '{req.room}'")
    if req.slot not in SLOTS:
        raise HTTPException(status_code=400, detail=f"Unknown slot '{req.slot}'")
    if not req.student_ids:
        raise HTTPException(status_code=400, detail="At least one student ID is required")

    info = ROOMS[req.room]
    group_size = len(req.student_ids)

    # Start the stopwatch for the availability-check work.
    start = time.perf_counter()

    def record_check_time():
        """Record how long the availability check took, into both metrics."""
        elapsed = time.perf_counter() - start
        AVAILABILITY_CHECK_SECONDS.observe(elapsed)
        AVAILABILITY_CHECK_SUMMARY.observe(elapsed)

        # Too big for this room?
    if group_size > info["capacity"]:
        record_check_time()
        raise HTTPException(status_code=400, detail={
            "reason": "group_too_large",
            "message": f"Room {req.room} holds at most {info['capacity']} students; your group has {group_size}.",
            "suggested_rooms": rooms_that_fit(group_size, req.slot),
        })

    # Too small for this room?
    if group_size < info["min_group"]:
        record_check_time()
        raise HTTPException(status_code=400, detail={
            "reason": "group_too_small",
            "message": f"Room {req.room} needs at least {info['min_group']} students; your group has {group_size}.",
            "suggested_rooms": rooms_that_fit(group_size, req.slot),
        })

    # Already booked?
    key = (req.room, req.slot)
    if key in bookings:
        record_check_time()
        raise HTTPException(status_code=409, detail={
            "reason": "double_booked",
            "message": f"Room {req.room} is already booked for {req.slot}.",
        })

    record_check_time()
    bookings[key] = req.student_ids
    BOOKINGS_TOTAL.inc()
    ROOMS_OCCUPIED.inc()
    return {
        "message": "Booked",
        "room": req.room,
        "slot": req.slot,
        "group_size": group_size,
    }


@app.get("/bookings")
def list_bookings():
    """Librarian view: everything currently booked."""
    return [
        {"room": room, "slot": slot, "student_ids": ids}
        for (room, slot), ids in sorted(bookings.items())
    ]


@app.delete("/bookings/{room}/{slot}")
def cancel(room: str, slot: str):
    """Cancel a booking, freeing that room+slot."""
    key = (room, slot)
    if key not in bookings:
        raise HTTPException(status_code=404, detail="No such booking")
    del bookings[key]
    ROOMS_OCCUPIED.dec()   # that room-slot is free again
    return {"message": "Cancelled", "room": room, "slot": slot}

@app.get("/metrics")
def metrics():
    """The page Prometheus scrapes. Plain text, not JSON."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)