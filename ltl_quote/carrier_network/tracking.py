# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""Dayton tracking activity codes and shipment progress milestone helpers."""

from __future__ import annotations

DAYTON_ACTIVITY_LABELS: dict[str, str] = {
	"ADD": "Picked Up",
	"ATD": "Attempted Delivery",
	"DLV": "Delivered",
	"DSC": "Arrived at Destination Center",
	"LOA": "Loaded at Service Center",
	"OFD": "Out for Delivery",
	"OK": "Delivered",
	"OTR": "In Transit",
	"PWD": "Delayed — Weather",
	"RP": "Replaced / Combined Shipment",
	"SBR": "Shipping Info Received",
	"SAD": "Trailer Dropped / Awaiting Action",
	"TPC": "Transferred to Partner",
	"UNL": "Unloaded at Service Center",
	# Normalized aliases used by some parsers / legacy responses
	"PICKED_UP": "Picked Up",
	"IN_TRANSIT": "In Transit",
	"OUT_FOR_DELIVERY": "Out for Delivery",
	"DELIVERED": "Delivered",
	# TForce Freight currentStatus / event codes
	"DL": "Delivered",
	"OF": "Out for Delivery",
	"PU": "Picked Up",
	"PK": "Picked Up",
	"AR": "Arrived at Service Center",
	"DP": "Departure",
	"IT": "In Transit",
	"EX": "Exception",
	"EXCEPTION": "Exception",
	"VOIDED": "Voided",
	# ArcBest Trace XML status aliases
	"PUP": "Picked Up",
	"DEL": "Delivered",
	"XCP": "Exception",
	"EXC": "Exception",
	# SMC3 Status API standardized codes / labels
	"PICKED UP": "Picked Up",
	"IN TRANSIT": "In Transit",
	"OUT FOR DELIVERY": "Out for Delivery",
	"INFO": "Info",
}

# Milestone keys used by the orange tracking timeline UI.
TIMELINE_MILESTONES: list[dict] = [
	{"key": "Requested", "label": "Requested", "icon": "fa-file-text-o"},
	{"key": "Booked", "label": "Booked", "icon": "fa-check-circle"},
	{"key": "PickedUp", "label": "Picked Up", "icon": "fa-truck"},
	{"key": "InTransit", "label": "In Transit", "icon": "fa-road"},
	{"key": "OutForDelivery", "label": "Out for Delivery", "icon": "fa-map-marker"},
	{"key": "Delivered", "label": "Delivered", "icon": "fa-flag-checkered"},
]

# Activity code → milestone index (0-based into TIMELINE_MILESTONES).
_ACTIVITY_MILESTONE_INDEX: dict[str, int] = {
	"SBR": 2,  # shipping info received → treat as approaching pickup
	"ADD": 2,  # Picked Up
	"PICKED_UP": 2,
	"OTR": 3,
	"LOA": 3,
	"UNL": 3,
	"DSC": 3,
	"TPC": 3,
	"RP": 3,
	"SAD": 3,
	"IN_TRANSIT": 3,
	"OFD": 4,
	"ATD": 4,
	"OUT_FOR_DELIVERY": 4,
	"DLV": 5,
	"OK": 5,
	"DELIVERED": 5,
	"DL": 5,
	"OF": 4,
	"PU": 2,
	"PK": 2,
	"PUP": 2,
	"AR": 3,
	"DP": 3,
	"IT": 3,
	"EX": 3,
	"EXCEPTION": 3,
	"XCP": 3,
	"EXC": 3,
	"DEL": 5,
	"VOIDED": 1,
	"PICKED UP": 2,
	"IN TRANSIT": 3,
	"OUT FOR DELIVERY": 4,
	"INFO": 3,
}

EXCEPTION_CODES = {"PWD", "EX", "EXCEPTION", "013", "XCP", "EXC"}

_STATUS_ORDER = [
	"Draft",
	"Booked",
	"Dispatched",
	"In Transit",
	"Out for Delivery",
	"Delivered",
]


def normalize_activity_code(code: str | None) -> str:
	return str(code or "").strip().upper()


def activity_label(code: str | None) -> str:
	value = normalize_activity_code(code)
	return DAYTON_ACTIVITY_LABELS.get(value) or value or "Cargo Movement Updated"


def is_exception_code(code: str | None) -> bool:
	return normalize_activity_code(code) in EXCEPTION_CODES


def milestone_index_for_code(code: str | None) -> int | None:
	value = normalize_activity_code(code)
	if value in _ACTIVITY_MILESTONE_INDEX:
		return _ACTIVITY_MILESTONE_INDEX[value]
	return None


def shipment_status_milestone_index(status: str | None) -> int:
	"""Baseline milestone from LTL Shipment.status before event codes are applied."""
	value = str(status or "").strip()
	if value == "Draft":
		return 0
	if value == "Booked":
		return 1
	if value == "Dispatched":
		return 2
	if value == "In Transit":
		return 3
	if value == "Out for Delivery":
		return 4
	if value == "Delivered":
		return 5
	if value in {"Cancelled", "Exception"}:
		return 1
	return 0


def shipment_status_from_activity(code: str | None) -> str | None:
	"""Map a Dayton activity code to LTL Shipment.status."""
	idx = milestone_index_for_code(code)
	if idx is None:
		return None
	if idx <= 2:
		return "In Transit" if idx == 2 else None
	if idx == 3:
		return "In Transit"
	if idx == 4:
		return "Out for Delivery"
	if idx >= 5:
		return "Delivered"
	return None


def build_timeline_milestones(
	*,
	shipment_status: str | None,
	has_quote_request: bool,
	event_codes: list[str] | None = None,
) -> tuple[str, list[dict]]:
	"""Return (current_milestone_key, milestone rows with completed/current flags)."""
	current_idx = 0
	if has_quote_request:
		current_idx = max(current_idx, 0)
	current_idx = max(current_idx, shipment_status_milestone_index(shipment_status))

	for code in event_codes or []:
		idx = milestone_index_for_code(code)
		if idx is not None:
			current_idx = max(current_idx, idx)

	current_idx = min(current_idx, len(TIMELINE_MILESTONES) - 1)
	rows: list[dict] = []
	for i, milestone in enumerate(TIMELINE_MILESTONES):
		rows.append(
			{
				**milestone,
				"completed": i < current_idx,
				"current": i == current_idx,
			}
		)
	return TIMELINE_MILESTONES[current_idx]["key"], rows


def flatten_tracking_events(events) -> list[dict]:
	"""Normalize child-table rows or dicts into a common event shape (newest first)."""
	normalized: list[dict] = []
	for row in events or []:
		if isinstance(row, dict):
			code = normalize_activity_code(row.get("status_code"))
			normalized.append(
				{
					"name": row.get("name"),
					"event_datetime": row.get("event_datetime"),
					"status_code": code,
					"status_description": row.get("status_description") or activity_label(code),
					"location": row.get("location") or "",
					"is_exception": int(row.get("is_exception") or 0) or int(is_exception_code(code)),
				}
			)
		else:
			code = normalize_activity_code(getattr(row, "status_code", None))
			normalized.append(
				{
					"name": getattr(row, "name", None),
					"event_datetime": getattr(row, "event_datetime", None),
					"status_code": code,
					"status_description": getattr(row, "status_description", None) or activity_label(code),
					"location": getattr(row, "location", None) or "",
					"is_exception": int(getattr(row, "is_exception", 0) or 0) or int(is_exception_code(code)),
				}
			)

	normalized.sort(key=lambda e: str(e.get("event_datetime") or ""), reverse=True)
	return normalized


def build_seed_tracking_events(shipment, quote=None) -> list[dict]:
	"""Display-only lifecycle events when Dayton has not returned scan history yet."""
	seeds: list[dict] = []
	origin_label = ""
	if quote is not None:
		city = getattr(quote, "origin_city", None) or ""
		state = getattr(quote, "origin_state", None) or ""
		zip_code = getattr(quote, "origin_zip", None) or ""
		origin_label = ", ".join(part for part in (city, state) if part)
		if origin_label and zip_code:
			origin_label = f"{origin_label} {zip_code}"
		elif zip_code:
			origin_label = str(zip_code)

	shipper_city = getattr(shipment, "bol_shipper_city", None) or ""
	shipper_state = getattr(shipment, "bol_shipper_state", None) or ""
	shipper_zip = getattr(shipment, "bol_shipper_postal_code", None) or ""
	if not origin_label:
		origin_label = ", ".join(part for part in (shipper_city, shipper_state) if part)
		if origin_label and shipper_zip:
			origin_label = f"{origin_label} {shipper_zip}"

	quote_name = getattr(shipment, "quote_request", None)
	if quote_name or quote is not None:
		when = None
		if quote is not None:
			when = getattr(quote, "creation", None)
		when = when or getattr(shipment, "booked_on", None) or getattr(shipment, "creation", None)
		seeds.append(
			{
				"name": None,
				"event_datetime": when,
				"status_code": "REQUESTED",
				"status_description": "Quote Requested",
				"location": origin_label or "",
				"is_exception": 0,
				"is_seed": 1,
			}
		)

	status = str(getattr(shipment, "status", None) or "")
	if status in {"Booked", "Dispatched", "In Transit", "Out for Delivery", "Delivered"}:
		seeds.append(
			{
				"name": None,
				"event_datetime": getattr(shipment, "booked_on", None) or getattr(shipment, "creation", None),
				"status_code": "BOOKED",
				"status_description": "Shipment Booked",
				"location": origin_label or "",
				"is_exception": 0,
				"is_seed": 1,
			}
		)

	pickup_number = str(getattr(shipment, "pickup_number", None) or "").strip()
	if pickup_number:
		seeds.append(
			{
				"name": None,
				"event_datetime": getattr(shipment, "pickup_ready", None)
				or getattr(shipment, "booked_on", None)
				or getattr(shipment, "creation", None),
				"status_code": "PICKUP_SCHEDULED",
				"status_description": f"Pickup Scheduled ({pickup_number})",
				"location": origin_label or "",
				"is_exception": 0,
				"is_seed": 1,
			}
		)

	seeds.sort(key=lambda e: str(e.get("event_datetime") or ""), reverse=True)
	return seeds
