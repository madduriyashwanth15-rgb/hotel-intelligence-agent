"""Deterministic synthetic data for one fictional property: Grand Hills Resort.

`generate(seed, anchor_date)` is a pure function: the same inputs always produce
the same dataset, with no I/O. The anchor date plays the role of "today" (the
hotel's business date): stays before it are history, stays spanning it are
in-house, stays after it are future bookings.

Privacy guarantees (enforced by tests):
- guest emails use RFC 2606 reserved domains (example.com/.org/.net);
- guest phone numbers use the NANP 555-0100..0199 range reserved for fiction;
- names are random combinations of generic first/last names.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from hotel_agent.db.models import (
    BookingChannel,
    ChargeCategory,
    HousekeepingStatus,
    LoyaltyTier,
    ReservationStatus,
    TaskPriority,
    TaskStatus,
    TaskType,
)
from hotel_agent.synthetic.names import EMAIL_DOMAINS, FIRST_NAMES, LAST_NAMES

GENERATOR_VERSION = "1.1.0"  # 1.1.0: synthetic staff directory

HOTEL_TZ = ZoneInfo("Asia/Kolkata")
CHECK_IN_TIME = time(14, 0)
CHECK_OUT_TIME = time(11, 0)
HISTORY_DAYS = 90
FUTURE_DAYS = 60
CANCELLATION_SHARE = 0.12
RETURNING_GUEST_SHARE = 0.15
OUT_OF_ORDER_ROOMS = 2
# Out-of-order rooms are blocked from bookings in this window around the anchor date.
MAINTENANCE_WINDOW = (timedelta(days=-2), timedelta(days=5))


@dataclass(frozen=True, slots=True)
class RoomTypeSpec:
    code: str
    name: str
    description: str
    max_occupancy: int
    base_rate: Decimal
    count: int


ROOM_TYPE_SPECS: tuple[RoomTypeSpec, ...] = (
    RoomTypeSpec("STD-K", "Standard King", "Garden-facing room with one king bed.",
                 2, Decimal("6500.00"), 24),
    RoomTypeSpec("STD-T", "Standard Twin", "Garden-facing room with two single beds.",
                 2, Decimal("6500.00"), 16),
    RoomTypeSpec("DLX", "Deluxe Valley View", "Larger room with a balcony over the valley.",
                 3, Decimal("9000.00"), 20),
    RoomTypeSpec("PRM", "Premier Garden Room", "Ground-access room with a private sit-out.",
                 3, Decimal("12500.00"), 10),
    RoomTypeSpec("STE", "Hillside Suite", "Separate living room and panoramic hill views.",
                 4, Decimal("21000.00"), 6),
    RoomTypeSpec("VIL", "Private Pool Villa", "Standalone villa with a plunge pool.",
                 4, Decimal("38000.00"), 4),
)  # fmt: skip

# --- Row types (field names match database columns) ---


@dataclass(frozen=True, slots=True)
class HotelRow:
    id: int
    code: str
    name: str
    city: str
    country_code: str
    timezone: str
    currency: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RoomTypeRow:
    id: int
    hotel_id: int
    code: str
    name: str
    description: str
    max_occupancy: int
    base_rate: Decimal


@dataclass(frozen=True, slots=True)
class RoomRow:
    id: int
    hotel_id: int
    room_type_id: int
    room_number: str
    floor: int
    housekeeping_status: str
    status_updated_at: datetime


@dataclass(frozen=True, slots=True)
class GuestRow:
    id: int
    first_name: str
    last_name: str
    email: str
    phone: str
    country_code: str
    loyalty_tier: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReservationRow:
    id: int
    confirmation_code: str
    hotel_id: int
    guest_id: int
    room_type_id: int
    room_id: int | None
    check_in_date: date
    check_out_date: date
    status: str
    channel: str
    adults: int
    children: int
    rate_per_night: Decimal
    total_amount: Decimal
    booked_at: datetime
    cancelled_at: datetime | None

    @property
    def nights(self) -> int:
        return (self.check_out_date - self.check_in_date).days


@dataclass(frozen=True, slots=True)
class FolioChargeRow:
    id: int
    reservation_id: int
    charge_date: date
    category: str
    description: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class HousekeepingTaskRow:
    id: int
    hotel_id: int
    room_id: int
    task_type: str
    priority: str
    status: str
    notes: str | None
    created_by: str
    created_at: datetime
    due_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class StaffMemberRow:
    id: int
    hotel_id: int
    staff_code: str
    display_name: str
    role: str
    is_active: bool


# Role-based display names only: these are simulated principals, not people.
STAFF_DIRECTORY: tuple[tuple[str, str, str, bool], ...] = (
    ("S-FD-01", "Front Desk Agent 01", "front_desk", True),
    ("S-FD-02", "Front Desk Agent 02", "front_desk", True),
    ("S-HK-01", "Room Attendant 01", "housekeeping", True),
    ("S-HS-01", "Housekeeping Supervisor 01", "housekeeping_supervisor", True),
    ("S-FOM-01", "Front Office Manager 01", "front_office_manager", True),
    ("S-DM-01", "Duty Manager 01", "duty_manager", True),
    ("S-DM-02", "Duty Manager 02", "duty_manager", True),
    ("S-GM-01", "General Manager 01", "general_manager", True),
    ("S-FD-99", "Former Front Desk Agent 99", "front_desk", False),
)


@dataclass(frozen=True, slots=True)
class DatasetMetadataRow:
    key: str
    value: str


@dataclass(frozen=True, slots=True)
class SyntheticDataset:
    seed: int
    anchor_date: date
    hotels: list[HotelRow]
    staff_members: list[StaffMemberRow]
    room_types: list[RoomTypeRow]
    rooms: list[RoomRow]
    guests: list[GuestRow]
    reservations: list[ReservationRow]
    folio_charges: list[FolioChargeRow]
    housekeeping_tasks: list[HousekeepingTaskRow]
    dataset_metadata: list[DatasetMetadataRow]

    def counts(self) -> dict[str, int]:
        return {
            "hotels": len(self.hotels),
            "staff_members": len(self.staff_members),
            "room_types": len(self.room_types),
            "rooms": len(self.rooms),
            "guests": len(self.guests),
            "reservations": len(self.reservations),
            "folio_charges": len(self.folio_charges),
            "housekeeping_tasks": len(self.housekeeping_tasks),
            "dataset_metadata": len(self.dataset_metadata),
        }


# --- Helpers ---


def _at(day: date, at: time) -> datetime:
    return datetime.combine(day, at, tzinfo=HOTEL_TZ)


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)


def _weighted[T](rng: random.Random, options: Sequence[tuple[T, float]]) -> T:
    values = [value for value, _ in options]
    weights = [weight for _, weight in options]
    return rng.choices(values, weights=weights, k=1)[0]


def _season_factor(day: date) -> Decimal:
    """Hill-resort seasonality: summer and year-end peaks, monsoon trough."""
    if day.month in (4, 5, 6):
        return Decimal("1.25")
    if day.month in (12, 1):
        return Decimal("1.20")
    if day.month in (7, 8, 9):
        return Decimal("0.85")
    return Decimal("1.00")


CHANNEL_FACTOR: dict[str, Decimal] = {
    BookingChannel.DIRECT: Decimal("1.00"),
    BookingChannel.OTA: Decimal("0.95"),
    BookingChannel.CORPORATE: Decimal("0.85"),
    BookingChannel.TRAVEL_AGENT: Decimal("0.90"),
    BookingChannel.WALK_IN: Decimal("1.05"),
}

NIGHTS_WEIGHTS = ((1, 25.0), (2, 30.0), (3, 20.0), (4, 10.0), (5, 7.0), (6, 4.0), (7, 4.0))
CHANNEL_WEIGHTS = (
    (BookingChannel.DIRECT, 30.0),
    (BookingChannel.OTA, 40.0),
    (BookingChannel.CORPORATE, 15.0),
    (BookingChannel.TRAVEL_AGENT, 10.0),
    (BookingChannel.WALK_IN, 5.0),
)
COUNTRY_WEIGHTS = (
    ("IN", 70.0), ("GB", 6.0), ("US", 6.0), ("AE", 5.0), ("SG", 4.0), ("DE", 4.0), ("AU", 5.0),
)  # fmt: skip
LOYALTY_WEIGHTS = (
    (LoyaltyTier.NONE, 60.0),
    (LoyaltyTier.SILVER, 22.0),
    (LoyaltyTier.GOLD, 13.0),
    (LoyaltyTier.PLATINUM, 5.0),
)
ANCILLARIES = (
    # (category, probability per night, min, max, description)
    (ChargeCategory.FOOD_BEVERAGE, 0.55, 800, 4500, "Restaurant and bar"),
    (ChargeCategory.MINIBAR, 0.20, 200, 1200, "In-room minibar"),
    (ChargeCategory.SPA, 0.08, 3000, 9000, "Spa treatment"),
    (ChargeCategory.LAUNDRY, 0.07, 300, 1500, "Laundry service"),
)
MAINTENANCE_NOTES = (
    "Synthetic: bathroom fixture repair",
    "Synthetic: air-conditioning unit service",
    "Synthetic: balcony door lock replacement",
)
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


@dataclass(slots=True)
class _Stay:
    """A reservation before guest and id assignment."""

    room_type: RoomTypeRow
    room_id: int | None
    check_in: date
    check_out: date
    status: ReservationStatus
    channel: BookingChannel
    adults: int
    children: int
    rate: Decimal
    booked_at: datetime
    cancelled_at: datetime | None
    guest_id: int = 0


# --- Generation steps ---


def _build_property(anchor: date) -> tuple[HotelRow, list[RoomTypeRow], list[RoomRow]]:
    created = _at(anchor - timedelta(days=HISTORY_DAYS + 365), time(9, 0))
    hotel = HotelRow(
        id=1,
        code="GHR",
        name="Grand Hills Resort",
        city="Hillcrest",
        country_code="IN",
        timezone=str(HOTEL_TZ),
        currency="INR",
        created_at=created,
    )
    room_types: list[RoomTypeRow] = []
    rooms: list[RoomRow] = []
    main_building = [(floor, idx) for floor in range(1, 5) for idx in range(1, 20)]
    slot = 0
    for type_id, spec in enumerate(ROOM_TYPE_SPECS, start=1):
        room_types.append(
            RoomTypeRow(type_id, hotel.id, spec.code, spec.name, spec.description,
                        spec.max_occupancy, spec.base_rate)
        )  # fmt: skip
        for villa_no in range(1, spec.count + 1):
            if spec.code == "VIL":
                number, floor = f"V{villa_no:02d}", 0
            else:
                floor, idx = main_building[slot]
                number = f"{floor}{idx:02d}"
                slot += 1
            rooms.append(
                RoomRow(len(rooms) + 1, hotel.id, type_id, number, floor,
                        HousekeepingStatus.CLEAN, created)
            )  # fmt: skip
    return hotel, room_types, rooms


def _occupancy_probability(day: date, anchor: date) -> float:
    p = 0.62
    if day.weekday() in (4, 5):  # Friday, Saturday nights
        p += 0.18
    days_ahead = (day - anchor).days
    if days_ahead > 0:  # future is only partially booked so far
        p *= max(0.30, 1.0 - days_ahead / 75)
    return p


def _booked_at(rng: random.Random, check_in: date, anchor: date, walk_in: bool) -> datetime:
    if walk_in:
        lead = 0
    else:
        lead = _weighted(rng, ((rng.randint(0, 3), 15.0), (rng.randint(4, 30), 45.0),
                               (rng.randint(31, 120), 40.0)))  # fmt: skip
    booked_day = check_in - timedelta(days=lead)
    if booked_day > anchor:  # bookings cannot be made after "today"
        booked_day = anchor - timedelta(days=rng.randint(0, 10))
    at = time(rng.randint(7, 22), rng.randint(0, 59))
    if walk_in or booked_day == check_in:
        at = min(at, time(13, 0))
    return _at(booked_day, at)


def _new_stay(
    rng: random.Random,
    anchor: date,
    room_type: RoomTypeRow,
    room_id: int | None,
    check_in: date,
    nights: int,
) -> _Stay:
    check_out = check_in + timedelta(days=nights)
    channel = _weighted(rng, CHANNEL_WEIGHTS)
    if channel == BookingChannel.WALK_IN and check_in > anchor:
        channel = BookingChannel.DIRECT  # walk-ins only exist on/before today
    adults = rng.randint(1, room_type.max_occupancy)
    children = rng.randint(0, room_type.max_occupancy - adults)
    rate = room_type.base_rate * _season_factor(check_in) * CHANNEL_FACTOR[channel]
    if check_in.weekday() in (4, 5):
        rate *= Decimal("1.10")
    booked_at = _booked_at(rng, check_in, anchor, channel == BookingChannel.WALK_IN)

    if check_out <= anchor:
        status = ReservationStatus.NO_SHOW if rng.random() < 0.04 else ReservationStatus.CHECKED_OUT
    elif check_in < anchor:
        status = ReservationStatus.CHECKED_IN
    # Arrivals on the anchor date stay confirmed until check-in time.
    else:
        status = ReservationStatus.CONFIRMED
    return _Stay(room_type, room_id, check_in, check_out, status, channel, adults, children,
                 _money(rate), booked_at, None)  # fmt: skip


def _generate_stays(
    rng: random.Random,
    anchor: date,
    room_types: list[RoomTypeRow],
    rooms: list[RoomRow],
    out_of_order: set[int],
) -> list[_Stay]:
    start = anchor - timedelta(days=HISTORY_DAYS)
    end = anchor + timedelta(days=FUTURE_DAYS)
    blocked_from, blocked_to = (anchor + d for d in MAINTENANCE_WINDOW)
    types_by_id = {rt.id: rt for rt in room_types}
    stays: list[_Stay] = []

    # Walk each room's calendar so assigned stays can never overlap.
    for room in rooms:
        cursor = start
        while cursor < end:
            if room.id in out_of_order and blocked_from <= cursor < blocked_to:
                cursor = blocked_to
                continue
            if rng.random() >= _occupancy_probability(cursor, anchor):
                cursor += timedelta(days=1)
                continue
            nights = _weighted(rng, NIGHTS_WEIGHTS)
            if room.id in out_of_order and cursor < blocked_from:
                nights = min(nights, (blocked_from - cursor).days)
            stay = _new_stay(rng, anchor, types_by_id[room.room_type_id], room.id, cursor, nights)
            stays.append(stay)
            cursor = stay.check_out

    # Cancellations don't hold a room, so they have no room assignment.
    for _ in range(round(len(stays) * CANCELLATION_SHARE)):
        room_type = rng.choice(room_types)
        check_in = start + timedelta(days=rng.randint(0, HISTORY_DAYS + FUTURE_DAYS - 1))
        stay = _new_stay(rng, anchor, room_type, None, check_in, _weighted(rng, NIGHTS_WEIGHTS))
        stay.status = ReservationStatus.CANCELLED
        latest = min(_at(check_in, time(12, 0)), _at(anchor, time(9, 0)))
        window = max(int((latest - stay.booked_at).total_seconds()), 0)
        stay.cancelled_at = stay.booked_at + timedelta(seconds=rng.randint(0, window))
        stays.append(stay)
    return stays


def _assign_guests(rng: random.Random, stays: list[_Stay]) -> list[GuestRow]:
    """Assign guests in booking order so returning guests only reuse earlier guests."""
    guests: list[GuestRow] = []
    stays.sort(key=lambda s: (s.booked_at, s.check_in, s.room_id or 0))
    for stay in stays:
        if guests and rng.random() < RETURNING_GUEST_SHARE:
            stay.guest_id = rng.choice(guests).id
            continue
        guest_id = len(guests) + 1
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        email = f"{first}.{last}.{guest_id}@{rng.choice(EMAIL_DOMAINS)}".lower()
        phone = f"+1-{rng.randint(201, 989)}-555-01{rng.randint(0, 99):02d}"
        guests.append(
            GuestRow(guest_id, first, last, email, phone, _weighted(rng, COUNTRY_WEIGHTS),
                     _weighted(rng, LOYALTY_WEIGHTS), stay.booked_at)
        )  # fmt: skip
        stay.guest_id = guest_id
    return guests


def _confirmation_codes(rng: random.Random, n: int) -> list[str]:
    codes: list[str] = []
    seen: set[str] = set()
    while len(codes) < n:
        code = "GHR-" + "".join(rng.choices(_CODE_ALPHABET, k=6))
        if code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def _build_reservations(
    rng: random.Random, hotel: HotelRow, stays: list[_Stay]
) -> list[ReservationRow]:
    stays.sort(key=lambda s: (s.check_in, s.room_id or 0, s.booked_at))
    codes = _confirmation_codes(rng, len(stays))
    return [
        ReservationRow(
            id=i,
            confirmation_code=codes[i - 1],
            hotel_id=hotel.id,
            guest_id=s.guest_id,
            room_type_id=s.room_type.id,
            room_id=s.room_id,
            check_in_date=s.check_in,
            check_out_date=s.check_out,
            status=s.status,
            channel=s.channel,
            adults=s.adults,
            children=s.children,
            rate_per_night=s.rate,
            total_amount=_money(s.rate * (s.check_out - s.check_in).days),
            booked_at=s.booked_at,
            cancelled_at=s.cancelled_at,
        )
        for i, s in enumerate(stays, start=1)
    ]


def _build_charges(
    rng: random.Random, anchor: date, reservations: list[ReservationRow]
) -> list[FolioChargeRow]:
    """Post charges for nights already consumed (before the anchor business date)."""
    charges: list[FolioChargeRow] = []
    for res in reservations:
        if res.status not in (ReservationStatus.CHECKED_OUT, ReservationStatus.CHECKED_IN):
            continue
        night = res.check_in_date
        while night < min(res.check_out_date, anchor):
            charges.append(
                FolioChargeRow(len(charges) + 1, res.id, night, ChargeCategory.ROOM,
                               "Room night", res.rate_per_night)
            )  # fmt: skip
            for category, prob, low, high, description in ANCILLARIES:
                if rng.random() < prob:
                    amount = _money(Decimal(rng.randint(low, high)))
                    charges.append(
                        FolioChargeRow(len(charges) + 1, res.id, night, category, description,
                                       amount)
                    )  # fmt: skip
            night += timedelta(days=1)
    return charges


def _room_states_and_tasks(
    rng: random.Random,
    anchor: date,
    hotel: HotelRow,
    rooms: list[RoomRow],
    out_of_order: set[int],
    reservations: list[ReservationRow],
) -> tuple[list[RoomRow], list[HousekeepingTaskRow]]:
    in_house = {r.room_id for r in reservations
                if r.status == ReservationStatus.CHECKED_IN and r.room_id is not None}  # fmt: skip
    departing = {r.room_id for r in reservations
                 if r.check_out_date == anchor and r.room_id is not None
                 and r.status == ReservationStatus.CHECKED_OUT}  # fmt: skip
    arriving = {r.room_id for r in reservations
                if r.check_in_date == anchor and r.room_id is not None}  # fmt: skip
    morning = _at(anchor, time(8, 0))
    due = _at(anchor, CHECK_IN_TIME)

    tasks: list[HousekeepingTaskRow] = []

    def add_task(
        room_id: int,
        task_type: TaskType,
        priority: TaskPriority,
        status: TaskStatus,
        created_at: datetime,
        due_at: datetime | None,
        completed_at: datetime | None = None,
        notes: str | None = None,
    ) -> None:
        tasks.append(
            HousekeepingTaskRow(len(tasks) + 1, hotel.id, room_id, task_type, priority, status,
                                notes, "system", created_at, due_at, completed_at)
        )  # fmt: skip

    # Completed turnover cleans for the last two weeks of departures.
    for res in reservations:
        days_ago = (anchor - res.check_out_date).days
        if res.status == ReservationStatus.CHECKED_OUT and 0 < days_ago <= 14 and res.room_id:
            created = _at(res.check_out_date, CHECK_OUT_TIME)
            add_task(res.room_id, TaskType.CLEANING, TaskPriority.NORMAL, TaskStatus.DONE,
                     created, _at(res.check_out_date, CHECK_IN_TIME),
                     created + timedelta(minutes=rng.randint(35, 150)))  # fmt: skip

    updated: list[RoomRow] = []
    for room in rooms:
        if room.id in out_of_order:
            status = HousekeepingStatus.OUT_OF_ORDER
            add_task(room.id, TaskType.MAINTENANCE, TaskPriority.URGENT, TaskStatus.IN_PROGRESS,
                     _at(anchor + MAINTENANCE_WINDOW[0], time(10, 0)),
                     _at(anchor + MAINTENANCE_WINDOW[1], time(12, 0)),
                     notes=rng.choice(MAINTENANCE_NOTES))  # fmt: skip
        elif room.id in departing:
            status = HousekeepingStatus.DIRTY
        elif room.id in in_house:
            status = HousekeepingStatus.DIRTY if rng.random() < 0.5 else HousekeepingStatus.CLEAN
        else:
            status = _weighted(rng, ((HousekeepingStatus.CLEAN, 50.0),
                                     (HousekeepingStatus.INSPECTED, 40.0),
                                     (HousekeepingStatus.DIRTY, 10.0)))  # fmt: skip
        if status == HousekeepingStatus.DIRTY:
            priority = TaskPriority.HIGH if room.id in arriving else TaskPriority.NORMAL
            add_task(room.id, TaskType.CLEANING, priority,
                     _weighted(rng, ((TaskStatus.OPEN, 70.0), (TaskStatus.IN_PROGRESS, 30.0))),
                     morning, due)  # fmt: skip
        elif status == HousekeepingStatus.CLEAN and room.id in arriving:
            add_task(room.id, TaskType.INSPECTION, TaskPriority.NORMAL, TaskStatus.OPEN,
                     morning, due)  # fmt: skip
        updated.append(
            RoomRow(room.id, room.hotel_id, room.room_type_id, room.room_number, room.floor,
                    status, morning + timedelta(minutes=rng.randint(0, 180)))
        )  # fmt: skip
    return updated, tasks


def generate(seed: int, anchor_date: date) -> SyntheticDataset:
    """Build the full synthetic dataset for Grand Hills Resort."""
    rng = random.Random(seed)
    hotel, room_types, rooms = _build_property(anchor_date)
    out_of_order = {room.id for room in rng.sample(rooms, OUT_OF_ORDER_ROOMS)}
    stays = _generate_stays(rng, anchor_date, room_types, rooms, out_of_order)
    guests = _assign_guests(rng, stays)
    reservations = _build_reservations(rng, hotel, stays)
    charges = _build_charges(rng, anchor_date, reservations)
    rooms, tasks = _room_states_and_tasks(rng, anchor_date, hotel, rooms, out_of_order,
                                          reservations)  # fmt: skip
    metadata = [
        DatasetMetadataRow("synthetic", "true"),
        DatasetMetadataRow("notice", "All data is synthetic. No real guests, staff or customers."),
        DatasetMetadataRow("generator_version", GENERATOR_VERSION),
        DatasetMetadataRow("seed", str(seed)),
        DatasetMetadataRow("anchor_date", anchor_date.isoformat()),
    ]
    staff = [
        StaffMemberRow(i, hotel.id, code, name, role, active)
        for i, (code, name, role, active) in enumerate(STAFF_DIRECTORY, start=1)
    ]
    return SyntheticDataset(seed, anchor_date, [hotel], staff, room_types, rooms, guests,
                            reservations, charges, tasks, metadata)  # fmt: skip
