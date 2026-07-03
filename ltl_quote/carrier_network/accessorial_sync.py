"""Populate per-carrier accessorial mappings from carrier APIs / documented codes.

The runtime rating path reads the ``accessorial_mappings`` child table on each
``LTL Carrier``. This module fills that table:

- Dayton: live REST ``GET /api/Shipping/Accessorials`` catalog (grouped Pickup /
  Delivery / Shipment Characteristics). Internal accessorials are matched to the
  carrier's Delivery Service codes (e.g. ``LIFT``, ``RESID``) by description. These
  string codes are what ``/api/Rates`` actually accepts (integer codes come back as
  ``ERROR``). Documented defaults are used if the API is unreachable.
- ArcBest: no accessorial-list endpoint exists, so the documented ``Acc_*`` XML flags
  are seeded; rows remain editable in the UI afterwards.
"""

from __future__ import annotations

import frappe
import requests

# internal accessorial code -> (carrier code sent to the rate API, description).
# Dayton Delivery Service codes from GET /api/Shipping/Accessorials.
DAYTON_SEED: dict[str, tuple[str, str]] = {
	"LIFTGATE": ("LIFT", "Liftgate Trailer for Delivery"),
	"RESIDENTIAL": ("RESID", "Residential Delivery"),
	"APPOINTMENT": ("NOT", "Appointment Needed or Call Before"),
	"INSIDE_DELIVERY": ("IDC", "Delivery Inside of Facility"),
	"LIMITED_ACCESS": ("LIMIT", "Limited Access"),
}

ARCBEST_SEED: dict[str, tuple[str, str]] = {
	"LIFTGATE": ("Acc_GRD_DEL", "Ground Delivery / Liftgate"),
	"RESIDENTIAL": ("Acc_RDEL", "Residential Delivery"),
	"HAZMAT": ("Acc_HAZ", "Hazardous Materials"),
	"APPOINTMENT": ("Acc_APPT", "Delivery Appointment"),
	"INSIDE_DELIVERY": ("Acc_IDEL", "Inside Delivery"),
	"LIMITED_ACCESS": ("Acc_LAD", "Limited Access Delivery"),
}

# keywords used to match a live carrier catalog description to an internal code
_DELIVERY_KEYWORDS: dict[str, str] = {
	"LIFTGATE": "liftgate",
	"RESIDENTIAL": "residential",
	"APPOINTMENT": "appointment",
	"INSIDE_DELIVERY": "inside",
	"LIMITED_ACCESS": "limited access",
}

DAYTON_ACCESSORIALS_PATH = "/api/Shipping/Accessorials"
DAYTON_DEFAULT_BASE_URL = "https://api.daytonfreight.com"
FETCH_TIMEOUT = 15


def sync_carrier_accessorials(carrier_doc, seed_only: bool = False) -> dict:
	"""Add/refresh accessorial mapping rows on ``carrier_doc`` (does not save).

	Returns a summary dict: ``{added, updated, skipped, source, message}``.
	"""
	connector = (getattr(carrier_doc, "connector_type", None) or "").strip()

	if connector == "Dayton":
		catalog = None if seed_only else _fetch_dayton_catalog(carrier_doc)
		desired = _dayton_desired_map(catalog)
		source = "Fetched" if catalog else "Seeded"
		return _apply_map(carrier_doc, desired, source=source, label="Dayton")

	if connector == "ArcBest API":
		return _apply_map(
			carrier_doc,
			ARCBEST_SEED,
			source="Seeded",
			label="ArcBest",
			extra_note="ArcBest exposes no accessorial-list API endpoint; seeded from documented ARC 111 flags.",
		)

	return {
		"added": 0,
		"updated": 0,
		"skipped": 0,
		"source": None,
		"message": f"No accessorial catalog is available for connector '{connector or 'Mock'}'.",
	}


def _apply_map(
	carrier_doc,
	desired: dict[str, tuple[str, str]],
	source: str,
	label: str,
	extra_note: str = "",
) -> dict:
	"""Upsert desired mappings: add missing, refresh auto rows, preserve manual edits."""
	existing_by_code = {
		(row.accessorial_code or row.accessorial): row
		for row in (carrier_doc.get("accessorial_mappings") or [])
	}

	added = updated = skipped = 0
	for internal_code, (carrier_code, carrier_name) in desired.items():
		if not frappe.db.exists("LTL Accessorial", internal_code):
			continue

		row = existing_by_code.get(internal_code)
		if row:
			if (row.source or "Manual") == "Manual":
				skipped += 1
				continue
			row.carrier_accessorial_code = carrier_code
			row.carrier_accessorial_name = carrier_name
			row.source = source
			updated += 1
		else:
			carrier_doc.append(
				"accessorial_mappings",
				{
					"accessorial": internal_code,
					"accessorial_code": internal_code,
					"carrier_accessorial_code": carrier_code,
					"carrier_accessorial_name": carrier_name,
					"enabled": 1,
					"source": source,
				},
			)
			added += 1

	message = (
		f"{label}: added {added}, updated {updated}, preserved {skipped} manual row(s) ({source})."
	)
	if extra_note:
		message = f"{message} {extra_note}"

	return {"added": added, "updated": updated, "skipped": skipped, "source": source, "message": message}


def _dayton_desired_map(catalog: list[dict] | None) -> dict[str, tuple[str, str]]:
	"""Build the internal->carrier map, overriding seed defaults with live catalog codes."""
	desired = dict(DAYTON_SEED)
	if not catalog:
		return desired

	for internal_code, keyword in _DELIVERY_KEYWORDS.items():
		match = _find_delivery_match(catalog, keyword)
		if match and match.get("code"):
			desired[internal_code] = (match["code"], match.get("description") or "")
	return desired


def _find_delivery_match(catalog: list[dict], keyword: str) -> dict | None:
	"""Find a catalog entry by description keyword, preferring Delivery Services."""
	delivery = [c for c in catalog if "delivery" in (c.get("group") or "").lower()]
	for pool in (delivery, catalog):
		for entry in pool:
			if keyword in (entry.get("description") or "").lower():
				return entry
	return None


def _fetch_dayton_catalog(carrier_doc) -> list[dict] | None:
	"""Best-effort live REST GetAccessorials fetch. Returns a flat list or None on failure."""
	try:
		username = carrier_doc.get_password("api_key", raise_exception=False) or ""
		password = carrier_doc.get_password("api_secret", raise_exception=False) or ""
	except Exception:
		username = password = ""

	base_url = (carrier_doc.get("api_base_url") or DAYTON_DEFAULT_BASE_URL).rstrip("/")
	auth = (username[:10], password) if username and password else None

	try:
		response = requests.get(
			f"{base_url}{DAYTON_ACCESSORIALS_PATH}",
			headers={"Accept": "application/json"},
			auth=auth,
			timeout=FETCH_TIMEOUT,
		)
		if response.status_code != 200:
			return None
		return _flatten_dayton_catalog(response.json())
	except requests.exceptions.RequestException as e:
		frappe.log_error(message=str(e), title="Dayton Accessorials fetch failed")
		return None
	except Exception as e:
		frappe.log_error(message=str(e), title="Dayton Accessorials parse failed")
		return None


def _flatten_dayton_catalog(data: dict) -> list[dict] | None:
	"""Flatten {'accessorials': {'Delivery Services': [{code, description}], ...}}."""
	groups = (data or {}).get("accessorials") or {}
	catalog: list[dict] = []
	for group_name, rows in groups.items():
		for row in rows or []:
			if not isinstance(row, dict):
				continue
			catalog.append(
				{
					"code": row.get("code"),
					"description": row.get("description"),
					"group": group_name,
				}
			)
	return catalog or None
