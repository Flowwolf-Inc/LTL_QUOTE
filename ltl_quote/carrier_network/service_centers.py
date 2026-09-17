"""Lookup helpers for Dayton Service Center catalog (lat/lng terminals)."""

from __future__ import annotations

import frappe

from ltl_quote.utils.location import normalize_us_state, normalize_us_zip

CACHE_KEY = "ltl_dayton_service_centers_v1"
CACHE_TTL = 3600


def _row_to_dict(row) -> dict:
	"""Normalize a DB row / dict into the public lookup shape."""
	if not row:
		return {}
	if hasattr(row, "get"):
		data = dict(row)
	else:
		data = {
			"center_id": getattr(row, "center_id", None),
			"center_name": getattr(row, "center_name", None),
			"city": getattr(row, "city", None),
			"state": getattr(row, "state", None),
			"zip": getattr(row, "zip", None),
			"address1": getattr(row, "address1", None),
			"phone": getattr(row, "phone", None),
			"lat": getattr(row, "lat", None),
			"lng": getattr(row, "lng", None),
			"center_number": getattr(row, "center_number", None),
		}

	center_id = str(data.get("center_id") or data.get("id") or data.get("name") or "").strip().upper()
	center_name = str(data.get("center_name") or data.get("name") or "").strip()
	city = str(data.get("city") or "").strip()
	state = normalize_us_state(data.get("state"))
	zip_code = normalize_us_zip(data.get("zip")) or str(data.get("zip") or "").strip()

	lat = data.get("lat")
	lng = data.get("lng")
	try:
		lat_f = float(lat) if lat not in (None, "") else None
	except (TypeError, ValueError):
		lat_f = None
	try:
		lng_f = float(lng) if lng not in (None, "") else None
	except (TypeError, ValueError):
		lng_f = None

	label_parts = []
	if center_name:
		label_parts.append(center_name)
	if center_id:
		label_parts.append(f"({center_id})")
	label = " ".join(label_parts).strip()
	if not label:
		city_state = ", ".join(part for part in (city, state) if part)
		label = city_state or center_id or "—"

	out = {
		"id": center_id,
		"name": center_name,
		"city": city,
		"state": state,
		"zip": zip_code,
		"address1": str(data.get("address1") or "").strip(),
		"phone": str(data.get("phone") or "").strip(),
		"label": label,
		"center_number": data.get("center_number"),
	}
	if lat_f is not None:
		out["lat"] = lat_f
	if lng_f is not None:
		out["lng"] = lng_f
	return out


def clear_service_center_cache() -> None:
	frappe.cache.delete_value(CACHE_KEY)


def get_all_service_centers() -> list[dict]:
	"""Return all synced service centers (cached)."""
	if not frappe.db.exists("DocType", "Dayton Service Center"):
		return []

	cached = frappe.cache.get_value(CACHE_KEY)
	if isinstance(cached, list):
		return cached

	rows = frappe.get_all(
		"Dayton Service Center",
		fields=[
			"name",
			"center_id",
			"center_number",
			"center_name",
			"address1",
			"address2",
			"city",
			"state",
			"zip",
			"phone",
			"toll_free",
			"fax",
			"lat",
			"lng",
		],
		order_by="center_id asc",
	)
	result = [_row_to_dict(row) for row in rows]
	frappe.cache.set_value(CACHE_KEY, result, expires_in_sec=CACHE_TTL)
	return result


def lookup_service_center(
	id: str | None = None,
	city: str | None = None,
	state: str | None = None,
	zip_code: str | None = None,
) -> dict:
	"""Resolve a Dayton terminal by id, then city+state, then zip.

	Returns a dict with id/name/city/state/zip/lat/lng/label, or {}.
	"""
	centers = get_all_service_centers()
	if not centers:
		return {}

	center_id = str(id or "").strip().upper()
	if center_id:
		for row in centers:
			if row.get("id") == center_id:
				return row

	want_city = str(city or "").strip().upper()
	want_state = normalize_us_state(state)
	if want_city and want_state:
		for row in centers:
			if str(row.get("city") or "").strip().upper() == want_city and row.get("state") == want_state:
				return row
		# Also match when city equals terminal name (e.g. "Dayton" / OH).
		for row in centers:
			if (
				str(row.get("name") or "").strip().upper() == want_city
				and row.get("state") == want_state
			):
				return row

	want_zip = normalize_us_zip(zip_code)
	if want_zip:
		for row in centers:
			if normalize_us_zip(row.get("zip")) == want_zip:
				return row

	return {}


def attach_service_center_coordinates(place: dict | None) -> dict:
	"""Enrich a place dict with lat/lng from the service center catalog when possible."""
	place = dict(place or {})
	if place.get("lat") is not None and place.get("lng") is not None:
		return place

	matched = lookup_service_center(
		id=place.get("service_center_id") or place.get("id"),
		city=place.get("city"),
		state=place.get("state"),
		zip_code=place.get("zip"),
	)
	if not matched:
		return place

	if matched.get("lat") is not None and matched.get("lng") is not None:
		place["lat"] = matched["lat"]
		place["lng"] = matched["lng"]
	if matched.get("id"):
		place["service_center_id"] = matched["id"]
	if matched.get("label") and (
		not place.get("label") or place.get("label") in {"—", place.get("city")}
	):
		place["label"] = matched["label"]
	return place
