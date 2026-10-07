"""Guarantees of the synthetic data generator (pure Python, no database)."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from itertools import pairwise

import pytest

from hotel_agent.db.models import (
    ACTIVE_RESERVATION_STATUSES,
    ChargeCategory,
    HousekeepingStatus,
    ReservationStatus,
    TaskStatus,
    TaskType,
)
from hotel_agent.synthetic.generator import ROOM_TYPE_SPECS, SyntheticDataset, generate
from hotel_agent.synthetic.names import EMAIL_DOMAINS, FIRST_NAMES, LAST_NAMES

ANCHOR = date(2026, 10, 1)
SEEDS = (1, 42, 2024)
FICTIONAL_PHONE = re.compile(r"^\+1-\d{3}-555-01\d{2}$")


@pytest.fixture(scope="module", params=SEEDS, ids=[f"seed{s}" for s in SEEDS])
def ds(request: pytest.FixtureRequest) -> SyntheticDataset:
    return generate(request.param, ANCHOR)


# --- Determinism ---


def test_same_seed_is_reproducible() -> None:
    assert generate(42, ANCHOR) == generate(42, ANCHOR)


def test_different_seed_or_anchor_changes_data() -> None:
    base = generate(42, ANCHOR)
    assert generate(43, ANCHOR).reservations != base.reservations
    assert generate(42, ANCHOR + timedelta(days=1)).reservations != base.reservations


# --- Property ---


def test_single_synthetic_hotel(ds: SyntheticDataset) -> None:
    assert len(ds.hotels) == 1
    hotel = ds.hotels[0]
    assert hotel.name == "Grand Hills Resort"
    assert hotel.currency == "INR"
    assert hotel.timezone == "Asia/Kolkata"


def test_room_inventory(ds: SyntheticDataset) -> None:
    assert len(ds.rooms) == sum(spec.count for spec in ROOM_TYPE_SPECS)
    assert len({r.room_number for r in ds.rooms}) == len(ds.rooms)
    per_type = Counter(r.room_type_id for r in ds.rooms)
    for rt, spec in zip(ds.room_types, ROOM_TYPE_SPECS, strict=True):
        assert per_type[rt.id] == spec.count


def test_dataset_is_marked_synthetic(ds: SyntheticDataset) -> None:
    meta = {m.key: m.value for m in ds.dataset_metadata}
    assert meta["synthetic"] == "true"
    assert meta["seed"] == str(ds.seed)
    assert meta["anchor_date"] == ANCHOR.isoformat()


# --- Referential integrity ---


def test_foreign_keys_resolve(ds: SyntheticDataset) -> None:
    hotel_ids = {h.id for h in ds.hotels}
    room_type_ids = {rt.id for rt in ds.room_types}
    rooms = {r.id: r for r in ds.rooms}
    guest_ids = {g.id for g in ds.guests}
    reservation_ids = {r.id for r in ds.reservations}

    assert all(rt.hotel_id in hotel_ids for rt in ds.room_types)
    assert all(r.hotel_id in hotel_ids and r.room_type_id in room_type_ids for r in ds.rooms)
    for res in ds.reservations:
        assert res.hotel_id in hotel_ids
        assert res.guest_id in guest_ids
        assert res.room_type_id in room_type_ids
        if res.room_id is not None:
            assert rooms[res.room_id].room_type_id == res.room_type_id
    assert all(c.reservation_id in reservation_ids for c in ds.folio_charges)
    assert all(t.room_id in rooms and t.hotel_id in hotel_ids for t in ds.housekeeping_tasks)


def test_ids_and_codes_unique(ds: SyntheticDataset) -> None:
    for rows in (ds.guests, ds.reservations, ds.folio_charges, ds.housekeeping_tasks):
        ids = [row.id for row in rows]
        assert ids == list(range(1, len(ids) + 1))
    codes = [r.confirmation_code for r in ds.reservations]
    assert len(set(codes)) == len(codes)


def test_every_guest_has_a_reservation(ds: SyntheticDataset) -> None:
    assert {r.guest_id for r in ds.reservations} == {g.id for g in ds.guests}


# --- Reservation rules ---


def test_no_room_is_double_booked(ds: SyntheticDataset) -> None:
    by_room: dict[int, list[tuple[date, date]]] = defaultdict(list)
    for r in ds.reservations:
        if r.room_id is not None and r.status in ACTIVE_RESERVATION_STATUSES:
            by_room[r.room_id].append((r.check_in_date, r.check_out_date))
    for stays in by_room.values():
        stays.sort()
        for (_, prev_out), (next_in, _) in pairwise(stays):
            assert next_in >= prev_out


def test_reservation_field_rules(ds: SyntheticDataset) -> None:
    max_occ = {rt.id: rt.max_occupancy for rt in ds.room_types}
    for r in ds.reservations:
        assert r.check_out_date > r.check_in_date
        assert r.adults >= 1
        assert r.children >= 0
        assert r.adults + r.children <= max_occ[r.room_type_id]
        assert r.rate_per_night > 0
        assert r.total_amount == r.rate_per_night * r.nights
        assert r.booked_at.date() <= r.check_in_date
        assert r.booked_at.date() <= ANCHOR


def test_cancellations(ds: SyntheticDataset) -> None:
    cancelled = [r for r in ds.reservations if r.status == ReservationStatus.CANCELLED]
    assert cancelled
    for r in ds.reservations:
        assert (r.status == ReservationStatus.CANCELLED) == (r.cancelled_at is not None)
    for r in cancelled:
        assert r.room_id is None
        assert r.cancelled_at is not None
        assert r.cancelled_at >= r.booked_at


def test_status_matches_business_date(ds: SyntheticDataset) -> None:
    for r in ds.reservations:
        if r.status in (ReservationStatus.CHECKED_OUT, ReservationStatus.NO_SHOW):
            assert r.check_out_date <= ANCHOR
        elif r.status == ReservationStatus.CHECKED_IN:
            assert r.check_in_date < ANCHOR < r.check_out_date
        elif r.status == ReservationStatus.CONFIRMED:
            assert r.check_in_date >= ANCHOR


def test_plausible_occupancy_on_business_date(ds: SyntheticDataset) -> None:
    occupied = {
        r.room_id
        for r in ds.reservations
        if r.status in ACTIVE_RESERVATION_STATUSES and r.check_in_date <= ANCHOR < r.check_out_date
    }
    assert 0.4 <= len(occupied) / len(ds.rooms) <= 0.95


def test_all_statuses_and_channels_present(ds: SyntheticDataset) -> None:
    assert {r.status for r in ds.reservations} == set(ReservationStatus)
    assert len({r.channel for r in ds.reservations}) == 5


# --- Folio charges ---


def test_charges_only_for_consumed_nights(ds: SyntheticDataset) -> None:
    by_id = {r.id: r for r in ds.reservations}
    room_nights: Counter[int] = Counter()
    for c in ds.folio_charges:
        res = by_id[c.reservation_id]
        assert res.status in (ReservationStatus.CHECKED_IN, ReservationStatus.CHECKED_OUT)
        assert res.check_in_date <= c.charge_date < min(res.check_out_date, ANCHOR)
        assert c.amount >= 0
        if c.category == ChargeCategory.ROOM:
            assert c.amount == res.rate_per_night
            room_nights[res.id] += 1
    for res in ds.reservations:
        if res.status == ReservationStatus.CHECKED_OUT:
            assert room_nights[res.id] == res.nights


# --- Rooms and housekeeping ---


def test_out_of_order_rooms_are_not_occupied(ds: SyntheticDataset) -> None:
    ooo = {r.id for r in ds.rooms if r.housekeeping_status == HousekeepingStatus.OUT_OF_ORDER}
    assert len(ooo) == 2
    for r in ds.reservations:
        if r.room_id in ooo and r.status in ACTIVE_RESERVATION_STATUSES:
            assert not (r.check_in_date <= ANCHOR < r.check_out_date)
    maintenance = {t.room_id for t in ds.housekeeping_tasks if t.task_type == TaskType.MAINTENANCE}
    assert maintenance == ooo


def test_dirty_rooms_have_an_active_cleaning_task(ds: SyntheticDataset) -> None:
    active = {
        t.room_id
        for t in ds.housekeeping_tasks
        if t.task_type == TaskType.CLEANING
        and t.status in (TaskStatus.OPEN, TaskStatus.IN_PROGRESS)
    }
    dirty = {r.id for r in ds.rooms if r.housekeeping_status == HousekeepingStatus.DIRTY}
    assert dirty == active


def test_task_completion_consistency(ds: SyntheticDataset) -> None:
    for t in ds.housekeeping_tasks:
        assert (t.status == TaskStatus.DONE) == (t.completed_at is not None)
        if t.completed_at is not None:
            assert t.completed_at >= t.created_at


# --- Privacy: nothing that could be a real person's contact details ---


def test_guest_contact_details_are_fictional(ds: SyntheticDataset) -> None:
    emails = [g.email for g in ds.guests]
    assert len(set(emails)) == len(emails)
    for g in ds.guests:
        assert g.email.rsplit("@", 1)[1] in EMAIL_DOMAINS
        assert FICTIONAL_PHONE.match(g.phone), g.phone
        assert g.first_name in FIRST_NAMES
        assert g.last_name in LAST_NAMES
