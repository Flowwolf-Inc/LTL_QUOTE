# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""US city/state resolution from ZIP codes for carrier booking."""

from __future__ import annotations

import re

import frappe
import requests

ZIP_LOOKUP_TIMEOUT = 5
ZIP_LOOKUP_CACHE_TTL = 60 * 60 * 24 * 30

US_STATE_ABBREVS = {
	"ALABAMA": "AL",
	"ALASKA": "AK",
	"ARIZONA": "AZ",
	"ARKANSAS": "AR",
	"CALIFORNIA": "CA",
	"COLORADO": "CO",
	"CONNECTICUT": "CT",
	"DELAWARE": "DE",
	"DISTRICT OF COLUMBIA": "DC",
	"FLORIDA": "FL",
	"GEORGIA": "GA",
	"HAWAII": "HI",
	"IDAHO": "ID",
	"ILLINOIS": "IL",
	"INDIANA": "IN",
	"IOWA": "IA",
	"KANSAS": "KS",
	"KENTUCKY": "KY",
	"LOUISIANA": "LA",
	"MAINE": "ME",
	"MARYLAND": "MD",
	"MASSACHUSETTS": "MA",
	"MICHIGAN": "MI",
	"MINNESOTA": "MN",
	"MISSISSIPPI": "MS",
	"MISSOURI": "MO",
	"MONTANA": "MT",
	"NEBRASKA": "NE",
	"NEVADA": "NV",
	"NEW HAMPSHIRE": "NH",
	"NEW JERSEY": "NJ",
	"NEW MEXICO": "NM",
	"NEW YORK": "NY",
	"NORTH CAROLINA": "NC",
	"NORTH DAKOTA": "ND",
	"OHIO": "OH",
	"OKLAHOMA": "OK",
	"OREGON": "OR",
	"PENNSYLVANIA": "PA",
	"RHODE ISLAND": "RI",
	"SOUTH CAROLINA": "SC",
	"SOUTH DAKOTA": "SD",
	"TENNESSEE": "TN",
	"TEXAS": "TX",
	"UTAH": "UT",
	"VERMONT": "VT",
	"VIRGINIA": "VA",
	"WASHINGTON": "WA",
	"WEST VIRGINIA": "WV",
	"WISCONSIN": "WI",
	"WYOMING": "WY",
}


def normalize_us_state(state: str | None) -> str:
	"""Return a two-letter US state abbreviation when possible."""
	if not state:
		return ""
	clean = str(state).strip().upper()
	if not clean:
		return ""
	if len(clean) == 2 and clean.isalpha():
		return clean
	return US_STATE_ABBREVS.get(clean, "")


def normalize_us_zip(zip_code: str | None) -> str:
	"""Extract the 5-digit ZIP from a US postal code."""
	if not zip_code:
		return ""
	digits = re.sub(r"\D", "", str(zip_code))
	return digits[:5] if len(digits) >= 5 else ""


def lookup_zip_location(zip_code: str | None) -> dict[str, str]:
	"""Resolve city, state, and coordinates from a US ZIP code."""
	normalized_zip = normalize_us_zip(zip_code)
	if not normalized_zip:
		return {}

	cache_key = f"ltl_zip_location_v2:{normalized_zip}"
	cached = frappe.cache.get_value(cache_key)
	if isinstance(cached, dict):
		return cached

	try:
		response = requests.get(
			f"https://api.zippopotam.us/us/{normalized_zip}",
			timeout=ZIP_LOOKUP_TIMEOUT,
		)
		if response.status_code != 200:
			return {}

		place = (response.json().get("places") or [{}])[0]
		lat = place.get("latitude")
		lng = place.get("longitude")
		result = {
			"city": str(place.get("place name") or "").strip(),
			"state": normalize_us_state(place.get("state abbreviation") or place.get("state")),
			"latitude": str(lat).strip() if lat not in (None, "") else "",
			"longitude": str(lng).strip() if lng not in (None, "") else "",
		}
		frappe.cache.set_value(cache_key, result, expires_in_sec=ZIP_LOOKUP_CACHE_TTL)
		return result
	except requests.exceptions.RequestException:
		return {}


def geocode_us_zip(zip_code: str | None) -> dict:
	"""Return city/state/lat/lng for a US ZIP (empty dict when unresolved)."""
	lookup = lookup_zip_location(zip_code)
	if not lookup:
		return {}
	lat = lookup.get("latitude")
	lng = lookup.get("longitude")
	try:
		lat_f = float(lat) if lat not in (None, "") else None
		lng_f = float(lng) if lng not in (None, "") else None
	except (TypeError, ValueError):
		lat_f = None
		lng_f = None
	out = {
		"city": lookup.get("city") or "",
		"state": lookup.get("state") or "",
		"zip": normalize_us_zip(zip_code),
	}
	if lat_f is not None and lng_f is not None:
		out["lat"] = lat_f
		out["lng"] = lng_f
	return out


def attach_zip_coordinates(place: dict | None) -> dict:
	"""Copy a place dict and add lat/lng from its zip when missing."""
	place = dict(place or {})
	if place.get("lat") is not None and place.get("lng") is not None:
		return place
	geo = geocode_us_zip(place.get("zip"))
	if geo.get("lat") is not None and geo.get("lng") is not None:
		place["lat"] = geo["lat"]
		place["lng"] = geo["lng"]
	if not place.get("city") and geo.get("city"):
		place["city"] = geo["city"]
	if not place.get("state") and geo.get("state"):
		place["state"] = geo["state"]
	return place


def resolve_us_location(
	zip_code: str | None,
	city: str | None = None,
	state: str | None = None,
) -> tuple[str, str]:
	"""Fill missing city/state using ZIP lookup and normalize state abbreviations."""
	resolved_city = str(city or "").strip()
	resolved_state = normalize_us_state(state)

	if resolved_city and resolved_state:
		return resolved_city, resolved_state

	lookup = lookup_zip_location(zip_code)
	if not resolved_city:
		resolved_city = lookup.get("city", "")
	if not resolved_state:
		resolved_state = lookup.get("state", "")

	return resolved_city, resolved_state


def enrich_location_fields(doc, prefix: str) -> None:
	"""Populate missing city/state fields on a document from its ZIP code."""
	city_field = f"{prefix}_city"
	state_field = f"{prefix}_state"
	zip_field = f"{prefix}_zip"

	city, state = resolve_us_location(doc.get(zip_field), doc.get(city_field), doc.get(state_field))
	if city and not doc.get(city_field):
		doc.set(city_field, city)
	if state and not doc.get(state_field):
		doc.set(state_field, state)
