"""Populate per-carrier accessorial mappings from carrier APIs / documented codes.

The runtime rating path reads the ``accessorial_mappings`` child table on each
``LTL Carrier``. This module fills that table:

- Dayton: live REST ``GET /api/Shipping/Accessorials`` catalog (grouped Pickup /
  Delivery / Shipment Characteristics). Internal accessorials are matched to
  Pickup and Delivery service codes separately (e.g. ``LFTP`` vs ``LIFT``) by
  group + description keywords. These string codes are what ``/api/Rates``
  accepts (integer codes come back as ``ERROR``). Documented defaults are used
  if the API is unreachable.
- ArcBest: no accessorial-list endpoint exists, so the documented ``Acc_*`` XML
  flags are seeded; rows remain editable in the UI afterwards.
"""

from __future__ import annotations

import frappe
import requests

# internal accessorial code -> (carrier code, description) for Delivery / load.
DAYTON_DELIVERY_SEED: dict[str, tuple[str, str]] = {
	"LIFTGATE": ("LIFT", "Liftgate Trailer for Delivery"),
	"RESIDENTIAL": ("RESID", "Residential Delivery"),
	"APPOINTMENT": ("NOT", "Appointment Needed or Call Before"),
	"INSIDE_DELIVERY": ("IDC", "Delivery Inside of Facility"),
	"LIMITED_ACCESS": ("LIMIT", "Limited Access"),
}

# Pickup Services counterparts (same internals, different Dayton rate codes).
DAYTON_PICKUP_SEED: dict[str, tuple[str, str]] = {
	"LIFTGATE": ("LFTP", "Liftgate Trailer for Pickup"),
	"RESIDENTIAL": ("RESPU", "Residential Pickup"),
	"APPOINTMENT": ("NOTP", "Appointment Needed or Call Before Pickup"),
	"INSIDE_DELIVERY": ("IPU", "Pickup Inside of Facility"),
	"LIMITED_ACCESS": ("LIMITP", "Limited Access Pickup"),
}

# Backward-compatible alias used by install / older callers.
DAYTON_SEED = DAYTON_DELIVERY_SEED

ARCBEST_SEED: dict[str, tuple[str, str]] = {
	"LIFTGATE": ("Acc_GRD_DEL", "Ground Delivery / Liftgate"),
	"RESIDENTIAL": ("Acc_RDEL", "Residential Delivery"),
	"HAZMAT": ("Acc_HAZ", "Hazardous Materials"),
	"APPOINTMENT": ("Acc_APPT", "Delivery Appointment"),
	"INSIDE_DELIVERY": ("Acc_IDEL", "Inside Delivery"),
	"LIMITED_ACCESS": ("Acc_LAD", "Limited Access Delivery"),
}

# keywords used to match a live carrier catalog description to an internal code
_MATCH_KEYWORDS: dict[str, str] = {
	"LIFTGATE": "liftgate",
	"RESIDENTIAL": "residential",
	"APPOINTMENT": "appointment",
	"INSIDE_DELIVERY": "inside",
	"LIMITED_ACCESS": "limited access",
}

# Prefer these exact Dayton codes when multiple catalog rows match a keyword.
_PREFERRED_CODES: dict[tuple[str, str], str] = {
	("LIFTGATE", "pickup"): "LFTP",
	("LIFTGATE", "delivery"): "LIFT",
	("LIMITED_ACCESS", "pickup"): "LIMITP",
	("LIMITED_ACCESS", "delivery"): "LIMIT",
	("INSIDE_DELIVERY", "pickup"): "IPU",
	("INSIDE_DELIVERY", "delivery"): "IDC",
	("RESIDENTIAL", "pickup"): "RESPU",
	("RESIDENTIAL", "delivery"): "RESID",
	("APPOINTMENT", "pickup"): "NOTP",
	("APPOINTMENT", "delivery"): "NOT",
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
		# ArcBest flags are not pickup/delivery-split; blank service_group.
		desired = {code: (carrier, name, "") for code, (carrier, name) in ARCBEST_SEED.items()}
		return _apply_map(
			carrier_doc,
			desired,
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
	desired: dict[str, tuple[str, str, str]],
	source: str,
	label: str,
	extra_note: str = "",
) -> dict:
	"""Upsert desired mappings: add missing, refresh auto rows, preserve manual edits.

	``desired`` maps ``internal_code`` or ``internal_code|service_group`` to
	``(carrier_code, carrier_name, service_group)``.
	"""
	existing_by_key = {
		_mapping_key(
			row.accessorial_code or row.accessorial,
			getattr(row, "service_group", None),
		): row
		for row in (carrier_doc.get("accessorial_mappings") or [])
	}

	added = updated = skipped = 0
	desired_keys: set[str] = set()
	for _key, (carrier_code, carrier_name, service_group) in desired.items():
		# Keys may be "CODE" or "CODE|pickup" — prefer the tuple's service_group.
		internal_code = _key.split("|", 1)[0] if "|" in _key else _key
		if not frappe.db.exists("LTL Accessorial", internal_code):
			# Also try by accessorial_code field if name differs.
			if not frappe.db.exists("LTL Accessorial", {"accessorial_code": internal_code}):
				continue
			internal_name = frappe.db.get_value(
				"LTL Accessorial", {"accessorial_code": internal_code}, "name"
			)
		else:
			internal_name = internal_code

		row_key = _mapping_key(internal_code, service_group)
		desired_keys.add(row_key)
		row = existing_by_key.get(row_key)
		if row:
			if (row.source or "Manual") == "Manual":
				skipped += 1
				continue
			row.carrier_accessorial_code = carrier_code
			row.carrier_accessorial_name = carrier_name
			row.service_group = service_group or ""
			row.source = source
			updated += 1
		else:
			carrier_doc.append(
				"accessorial_mappings",
				{
					"accessorial": internal_name,
					"accessorial_code": internal_code,
					"service_group": service_group or "",
					"carrier_accessorial_code": carrier_code,
					"carrier_accessorial_name": carrier_name,
					"enabled": 1,
					"source": source,
				},
			)
			added += 1

	# Drop obsolete auto-synced rows (e.g. pre-group blank delivery seeds).
	removed = 0
	for row in list(carrier_doc.get("accessorial_mappings") or []):
		if (row.source or "Manual") == "Manual":
			continue
		row_key = _mapping_key(
			row.accessorial_code or row.accessorial,
			getattr(row, "service_group", None),
		)
		if row_key not in desired_keys:
			carrier_doc.remove(row)
			removed += 1

	message = (
		f"{label}: added {added}, updated {updated}, preserved {skipped} manual row(s) ({source})."
	)
	if removed:
		message = f"{message} Removed {removed} obsolete auto row(s)."
	if extra_note:
		message = f"{message} {extra_note}"

	return {
		"added": added,
		"updated": updated,
		"skipped": skipped,
		"removed": removed,
		"source": source,
		"message": message,
	}


def _mapping_key(internal_code: str | None, service_group: str | None) -> str:
	code = (internal_code or "").strip().upper()
	group = (service_group or "").strip().lower()
	return f"{code}|{group}" if group else code


def _dayton_desired_map(catalog: list[dict] | None) -> dict[str, tuple[str, str, str]]:
	"""Build internal->carrier map for pickup and delivery groups.

	Returns ``{ "CODE|pickup": (carrier, name, "pickup"), "CODE|delivery": ..., "CODE|load": ... }``.
	Load reuses delivery codes (shipment-level services).
	"""
	desired: dict[str, tuple[str, str, str]] = {}

	for internal_code, (carrier_code, carrier_name) in DAYTON_DELIVERY_SEED.items():
		desired[f"{internal_code}|delivery"] = (carrier_code, carrier_name, "delivery")
		desired[f"{internal_code}|load"] = (carrier_code, carrier_name, "load")

	for internal_code, (carrier_code, carrier_name) in DAYTON_PICKUP_SEED.items():
		desired[f"{internal_code}|pickup"] = (carrier_code, carrier_name, "pickup")

	if not catalog:
		return desired

	for internal_code, keyword in _MATCH_KEYWORDS.items():
		delivery_match = _find_group_match(catalog, keyword, "delivery", internal_code)
		if delivery_match and delivery_match.get("code"):
			entry = (
				str(delivery_match["code"]).strip(),
				delivery_match.get("description") or "",
				"delivery",
			)
			desired[f"{internal_code}|delivery"] = entry
			desired[f"{internal_code}|load"] = (entry[0], entry[1], "load")

		pickup_match = _find_group_match(catalog, keyword, "pickup", internal_code)
		if pickup_match and pickup_match.get("code"):
			desired[f"{internal_code}|pickup"] = (
				str(pickup_match["code"]).strip(),
				pickup_match.get("description") or "",
				"pickup",
			)

	return desired


def _find_group_match(
	catalog: list[dict],
	keyword: str,
	side: str,
	internal_code: str,
) -> dict | None:
	"""Find a catalog entry by description keyword within Pickup or Delivery services."""
	side = (side or "").lower()
	preferred = _PREFERRED_CODES.get((internal_code, side))

	pool = [
		c
		for c in catalog
		if side in (c.get("group") or "").lower()
		or (side == "delivery" and "delivery" in (c.get("group") or "").lower())
		or (side == "pickup" and "pickup" in (c.get("group") or "").lower())
	]
	# Prefer exact preferred code within the group.
	if preferred:
		for entry in pool:
			if str(entry.get("code") or "").strip().upper() == preferred.upper():
				return entry

	keyword_l = keyword.lower()
	for entry in pool:
		if keyword_l in (entry.get("description") or "").lower():
			return entry

	# Fallback: any group, but only if preferred code matches (avoids wrong side).
	if preferred:
		for entry in catalog:
			if str(entry.get("code") or "").strip().upper() == preferred.upper():
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
