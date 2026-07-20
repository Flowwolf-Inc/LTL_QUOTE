# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""Shared accessorial code normalization and carrier-specific mappings."""

from __future__ import annotations

from ltl_quote.carrier_network.adapters.base import AccessorialItem


def normalize_accessorial_code(code: str | None) -> str | None:
	if not code:
		return None
	return str(code).strip().upper()


def build_accessorial_items(rows) -> list[AccessorialItem]:
	"""Build AccessorialItem list from LTL Quote Request accessorial child rows."""
	items: list[AccessorialItem] = []
	for row in rows or []:
		code = normalize_accessorial_code(getattr(row, "accessorial_code", None))
		if not code and getattr(row, "accessorial", None):
			import frappe

			code = normalize_accessorial_code(
				frappe.db.get_value("LTL Accessorial", row.accessorial, "accessorial_code")
			)
		if not code:
			continue
		quantity = max(int(getattr(row, "quantity", None) or 1), 1)
		group = normalize_service_group(getattr(row, "service_group", None))
		items.append(AccessorialItem(code=code, quantity=quantity, service_group=group))
	return items


def build_accessorial_items_from_payload(rows: list[dict]) -> list[AccessorialItem]:
	items: list[AccessorialItem] = []
	for row in rows or []:
		if isinstance(row, dict):
			code = normalize_accessorial_code(row.get("accessorial_code") or row.get("code"))
			quantity = max(int(row.get("quantity") or row.get("qty") or 1), 1)
			group = normalize_service_group(row.get("service_group") or row.get("group"))
		else:
			code = normalize_accessorial_code(str(row))
			quantity = 1
			group = ""
		if code:
			items.append(AccessorialItem(code=code, quantity=quantity, service_group=group))
	return items


def unique_accessorial_codes(accessorials: list[AccessorialItem]) -> list[str]:
	"""Unique codes for flag-based carrier APIs (quantity enables the flag once)."""
	seen: set[str] = set()
	ordered: list[str] = []
	for item in accessorials:
		if item.code and item.code not in seen:
			seen.add(item.code)
			ordered.append(item.code)
	return ordered


def expanded_accessorial_codes(accessorials: list[AccessorialItem]) -> list[str]:
	"""Codes repeated by quantity for per-unit pricing (mock adapter)."""
	expanded: list[str] = []
	for item in accessorials:
		if not item.code:
			continue
		expanded.extend([item.code] * max(int(item.quantity or 1), 1))
	return expanded


def carrier_accessorial_map(carrier_doc) -> dict[str, str]:
	"""Read enabled accessorial mappings into {internal_code: carrier_code}.

	When multiple service-group rows exist for the same internal code, prefer
	delivery, then blank, then load, then pickup (backward-compatible default).
	"""
	by_group = carrier_accessorial_map_by_group(carrier_doc)
	mapping: dict[str, str] = {}
	preference = ("delivery", "", "load", "pickup")
	for internal_code in {code for code, _group in by_group}:
		for group in preference:
			if (internal_code, group) in by_group:
				mapping[internal_code] = by_group[(internal_code, group)]
				break
		else:
			for (code, _group), carrier_code in by_group.items():
				if code == internal_code:
					mapping[internal_code] = carrier_code
					break
	return mapping


def carrier_accessorial_map_by_group(carrier_doc) -> dict[tuple[str, str], str]:
	"""Read enabled mappings into {(internal_code, service_group): carrier_code}."""
	mapping: dict[tuple[str, str], str] = {}
	if not carrier_doc:
		return mapping
	for row in carrier_doc.get("accessorial_mappings") or []:
		if not getattr(row, "enabled", 1):
			continue
		internal_code = normalize_accessorial_code(
			getattr(row, "accessorial_code", None) or getattr(row, "accessorial", None)
		)
		carrier_code = (getattr(row, "carrier_accessorial_code", None) or "").strip()
		group = normalize_service_group(getattr(row, "service_group", None))
		if internal_code and carrier_code:
			mapping[(internal_code, group)] = carrier_code
	return mapping


def arcbest_accessorial_params(accessorials: list[AccessorialItem], carrier_doc) -> dict[str, str]:
	"""Build ArcBest XML flag params from the carrier's accessorial mapping table."""
	code_map = carrier_accessorial_map(carrier_doc)
	params: dict[str, str] = {}
	for item in accessorials:
		if not item.code or item.quantity < 1:
			continue
		param_name = code_map.get(normalize_accessorial_code(item.code))
		if param_name:
			params[param_name] = "Y"
	return params


def dayton_rate_accessorials(accessorials: list[AccessorialItem], carrier_doc) -> list:
	"""Map accessorial codes to Dayton service codes via the carrier mapping table.

	Respects ``AccessorialItem.service_group`` so pickup (e.g. ``LFTP``) and
	delivery (e.g. ``LIFT``) resolve independently when both are selected.

	Dayton's REST rate API accepts the string service codes returned by
	GET /api/Shipping/Accessorials (e.g. ``LIFT``, ``RESID``). Numeric-only codes are
	coerced to int for backward compatibility with any legacy integer mappings.
	"""
	by_group = carrier_accessorial_map_by_group(carrier_doc)
	fallback = carrier_accessorial_map(carrier_doc)
	codes: list = []
	seen: set = set()
	for item in accessorials:
		if not item.code or item.quantity < 1:
			continue
		internal = normalize_accessorial_code(item.code)
		if not internal:
			continue
		group = normalize_service_group(getattr(item, "service_group", None))
		mapped = None
		if group:
			mapped = by_group.get((internal, group))
			# Load services reuse delivery codes when no load-specific row exists.
			if not mapped and group == "load":
				mapped = by_group.get((internal, "delivery"))
		if not mapped:
			mapped = by_group.get((internal, "")) or fallback.get(internal)
		if not mapped:
			# Pass through Dayton catalog / response codes selected directly in the UI.
			mapped = _pass_through_dayton_code(internal)
		if not mapped:
			continue
		value = str(mapped).strip()
		if not value:
			continue
		code = int(value) if value.isdigit() else value
		if code not in seen:
			codes.append(code)
			seen.add(code)
	return codes


def _pass_through_dayton_code(code: str | None) -> str | None:
	"""Return code unchanged when it exists in synced Dayton catalogs."""
	value = normalize_accessorial_code(code)
	if not value:
		return None
	try:
		import frappe

		if frappe.db.exists("Dayton Accessorial", {"code": value}):
			return value
		if frappe.db.exists("Dayton Response Accessorial", value):
			return value
	except Exception:
		return None
	return None


# Dayton eBOL accessorial codes (NMFTA / Digital LTL Council), keyed by
# (internal_code, service_group). Pickup vs delivery must stay distinct because
# the UI reuses one internal code (e.g. LIFTGATE) on both sides.
DAYTON_BOL_ACCESSORIAL_MAP: dict[tuple[str, str], str] = {
	("LIFTGATE", "pickup"): "LFTP",
	("LIFTGATE", "delivery"): "LFTD",
	("LIFTGATE", "load"): "LFTD",
	("INSIDE_DELIVERY", "pickup"): "IPU",
	("INSIDE_DELIVERY", "delivery"): "IDL",
	("INSIDE_DELIVERY", "load"): "IDL",
	("RESIDENTIAL", "pickup"): "RES",
	("RESIDENTIAL", "delivery"): "RES",
	("RESIDENTIAL", "load"): "RES",
	("APPOINTMENT", "pickup"): "APTP",
	("APPOINTMENT", "delivery"): "APTD",
	("APPOINTMENT", "load"): "APTD",
	("LIMITED_ACCESS", "pickup"): "LTDAP",
	("LIMITED_ACCESS", "delivery"): "LTDAD",
	("LIMITED_ACCESS", "load"): "LTDAD",
	("HAZMAT", "pickup"): "HAZ",
	("HAZMAT", "delivery"): "HAZ",
	("HAZMAT", "load"): "HAZ",
}

DAYTON_BOL_ACCESSORIAL_DEFAULTS: dict[str, str] = {
	"LIFTGATE": "LFTD",
	"INSIDE_DELIVERY": "IDL",
	"RESIDENTIAL": "RES",
	"APPOINTMENT": "APTD",
	"LIMITED_ACCESS": "LTDAD",
	"HAZMAT": "HAZ",
	"COD": "COD",
	"EXPD": "EXPD",
	"FVC": "FVC",
	"OVR": "OVR",
	"MARK": "MARK",
	"SRT": "SRT",
	"SS": "SS",
	"TCS": "TCS",
	"PPD": "PPD",
	"PSC": "PSC",
	"PSH": "PSH",
	"PSN": "PSN",
	"REP": "REP",
	"MNC": "MNC",
	"INBD": "INBD",
}

DAYTON_BOL_CODE_LABELS: dict[str, str] = {
	"LFTP": "Liftgate Pickup",
	"LFTD": "Liftgate Delivery",
	"IPU": "Inside Pickup",
	"IDL": "Inside Delivery",
	"RES": "Residential Delivery",
	"APTP": "Appointment Pickup",
	"APTD": "Appointment Delivery",
	"LTDAP": "Limited Access Pickup",
	"LTDAD": "Limited Access Delivery",
	"HAZ": "Hazmat Handling",
	"COD": "Collect on Delivery",
	"EXPD": "Expedited",
	"FVC": "Full Value Coverage",
	"OVR": "Over Dimension",
	"MARK": "Marking / Tagging",
	"SRT": "Sort and Segregate",
	"SS": "Sort and Segregate",
	"TCS": "Time Critical Service",
	"PPD": "Protect from Freezing",
	"INBD": "Inbond",
}


def normalize_service_group(group: str | None) -> str:
	value = str(group or "").strip().lower()
	if value in {"pickup", "origin"}:
		return "pickup"
	if value in {"delivery", "destination"}:
		return "delivery"
	if value in {"load", "shipment", "freight"}:
		return "load"
	return ""


def map_dayton_bol_accessorial_code(internal_code: str | None, service_group: str | None = None) -> str | None:
	"""Map an internal accessorial (+ optional pickup/delivery/load group) to a Dayton eBOL code."""
	code = normalize_accessorial_code(internal_code)
	if not code:
		return None

	# Already a Dayton eBOL code.
	if code in DAYTON_BOL_CODE_LABELS or code in {
		"GTD_AM",
		"GTD_NOON",
		"GTD_PM",
		"LFTD",
		"LFTP",
		"IDL",
		"IPU",
		"LTDAD",
		"LTDAP",
		"APTD",
		"APTP",
	}:
		return code

	group = normalize_service_group(service_group)
	if group:
		mapped = DAYTON_BOL_ACCESSORIAL_MAP.get((code, group))
		if mapped:
			return mapped
	mapped = DAYTON_BOL_ACCESSORIAL_DEFAULTS.get(code)
	if mapped:
		return mapped

	# Only synced Dayton LTL Accessorial (eBOL NMFTA) codes may be sent on eBOL.
	if is_supported_ltl_bol_accessorial(code):
		return code
	return None


def dayton_bol_accessorial_codes(rows) -> list[str]:
	"""Unique Dayton eBOL accessorial codes from quote-request rows or payload dicts."""
	ordered: list[str] = []
	seen: set[str] = set()
	for row in rows or []:
		if isinstance(row, dict):
			internal = row.get("accessorial_code") or row.get("code") or row.get("accessorial")
			group = row.get("service_group") or row.get("group")
		else:
			internal = getattr(row, "accessorial_code", None) or getattr(row, "accessorial", None)
			group = getattr(row, "service_group", None)
		mapped = map_dayton_bol_accessorial_code(internal, group)
		if mapped and is_supported_ltl_bol_accessorial(mapped) and mapped not in seen:
			seen.add(mapped)
			ordered.append(mapped)
	return ordered


def dayton_bol_accessorial_labels(rows) -> list[str]:
	"""Human-readable labels for selected accessorials (prefer UI/master labels).

	When a mapped Dayton eBOL / response code exists in Dayton Response Accessorial,
	prefer that catalog description for logging and BOL special-instructions text.
	"""
	labels: list[str] = []
	seen: set[str] = set()
	response_labels = _response_accessorial_label_map()
	for row in rows or []:
		if isinstance(row, dict):
			internal = row.get("accessorial_code") or row.get("code") or row.get("accessorial")
			group = row.get("service_group") or row.get("group")
			label = str(row.get("label") or row.get("accessorial_name") or "").strip()
		else:
			internal = getattr(row, "accessorial_code", None) or getattr(row, "accessorial", None)
			group = getattr(row, "service_group", None)
			label = str(getattr(row, "accessorial_name", None) or "").strip()
		mapped = map_dayton_bol_accessorial_code(internal, group)
		if not mapped:
			continue
		display = (
			label
			or response_labels.get(mapped)
			or _ltl_bol_accessorial_label_map().get(mapped)
			or DAYTON_BOL_CODE_LABELS.get(mapped)
			or mapped
		)
		if display not in seen:
			seen.add(display)
			labels.append(display)
	return labels


def _response_accessorial_label_map() -> dict[str, str]:
	"""code -> description from synced Dayton Response Accessorial (best-effort)."""
	try:
		import frappe

		if not frappe.db:
			return {}
		rows = frappe.get_all(
			"Dayton Response Accessorial",
			fields=["code", "description"],
			limit_page_length=500,
		)
	except Exception:
		return {}
	return {
		str(row.code).strip().upper(): str(row.description).strip()
		for row in rows
		if row.code and row.description
	}


def _ltl_bol_accessorial_label_map() -> dict[str, str]:
	"""code -> description from synced Dayton LTL Accessorial (eBOL standard codes)."""
	try:
		import frappe

		if not frappe.db:
			return {}
		rows = frappe.get_all(
			"Dayton LTL Accessorial",
			fields=["code", "description"],
			limit_page_length=500,
		)
	except Exception:
		return {}
	return {
		str(row.code).strip().upper(): str(row.description).strip()
		for row in rows
		if row.code and row.description
	}


def is_supported_ltl_bol_accessorial(code: str | None) -> bool:
	"""Return True when code exists in synced Dayton LTL Accessorial catalog."""
	value = normalize_accessorial_code(code)
	if not value:
		return False
	try:
		import frappe

		return bool(frappe.db.exists("Dayton LTL Accessorial", value))
	except Exception:
		return value in DAYTON_BOL_CODE_LABELS


def enrich_dayton_bol_accessorial_codes(codes: list[str] | None) -> list[dict]:
	"""Attach Response Accessorial descriptions to BOL codes for logging/enrichment.

	Does not replace NMFTA BOL codes — returns ``[{code, description}]`` for callers
	that want catalog-backed labels alongside the payload codes list.
	"""
	response_labels = _response_accessorial_label_map()
	enriched: list[dict] = []
	for raw in codes or []:
		code = str(raw or "").strip().upper()
		if not code:
			continue
		enriched.append(
			{
				"code": code,
				"description": response_labels.get(code) or DAYTON_BOL_CODE_LABELS.get(code) or code,
			}
		)
	return enriched


def build_dayton_bol_special_instructions(rows, service_name: str = "Standard LTL") -> str:
	"""Build BOL specialInstructions from selected pickup/delivery/load accessorials."""
	labels = dayton_bol_accessorial_labels(rows)
	service = str(service_name or "Standard LTL").strip() or "Standard LTL"
	if labels:
		return f"Service: {service} | ACCESSORIALS: {', '.join(labels)}"
	return f"Service: {service}"


# NMFTA / Digital LTL Council limited-access subtype. Required when LTDAD/LTDAP
# is present. "Other-52" is the generic "Other" destination/origin type.
DAYTON_DEFAULT_LIMITED_ACCESS_TYPE = "Other-52"


# Dayton PUT /api/Pickup accepts these accessorial codes (per API docs).
DAYTON_PICKUP_API_ACCESSORIALS: set[str] = {
	"NOT",
	"RESIP",
	"AIRDEL",
	"CCVD",
	"LIFT",
	"RESID",
	"CVD",
	"LIMIT",
	*(f"EXLG{n:02d}" for n in range(7, 21)),
}

# Rate-catalog pickup codes that map to the pickup API vocabulary.
DAYTON_PICKUP_RATE_CODE_MAP: dict[str, str] = {
	"NOTP": "NOT",
	"RESPU": "RESIP",
	"RESIP": "RESIP",
	"LIFTPU": "LIFT",
	"LFTP": "LIFT",
	"LIMITP": "LIMIT",
	"LIMIT": "LIMIT",
	"LIFT": "LIFT",
	"RESID": "RESID",
	"AIRDEL": "AIRDEL",
	"CCVD": "CCVD",
	"CVD": "CVD",
}


def is_supported_pickup_api_accessorial(code: str | None) -> bool:
	value = normalize_accessorial_code(code)
	return bool(value and value in DAYTON_PICKUP_API_ACCESSORIALS)


def map_dayton_pickup_api_accessorial_code(code: str | None) -> str | None:
	"""Map a rate/internal accessorial code to a PUT /api/Pickup accessorial code."""
	value = normalize_accessorial_code(code)
	if not value:
		return None
	mapped = DAYTON_PICKUP_RATE_CODE_MAP.get(value, value)
	return mapped if is_supported_pickup_api_accessorial(mapped) else None


def dayton_pickup_accessorials(accessorials: list[AccessorialItem], carrier_doc) -> list[str]:
	"""Map quote accessorials to codes accepted by Dayton PUT /api/Pickup."""
	rate_codes = dayton_rate_accessorials(accessorials, carrier_doc)
	ordered: list[str] = []
	seen: set[str] = set()
	for raw in rate_codes:
		mapped = map_dayton_pickup_api_accessorial_code(str(raw))
		if mapped and mapped not in seen:
			seen.add(mapped)
			ordered.append(mapped)
	return ordered


def build_dayton_bol_accessorials_section(
	codes: list[str] | None = None,
	*,
	limited_access_origin: str | None = None,
	limited_access_destination: str | None = None,
) -> dict:
	"""Build the Dayton eBOL ``accessorials`` object, including required detail blocks."""
	codes = [
		str(code).strip().upper()
		for code in (codes or [])
		if str(code or "").strip() and is_supported_ltl_bol_accessorial(code)
	]
	section: dict = {
		"codes": codes,
		"hazardousDetails": {
			"emergencyContact": {},
		},
	}

	limited: dict[str, str] = {}
	if "LTDAP" in codes:
		limited["origin"] = (
			str(limited_access_origin or "").strip() or DAYTON_DEFAULT_LIMITED_ACCESS_TYPE
		)
	if "LTDAD" in codes:
		limited["destination"] = (
			str(limited_access_destination or "").strip() or DAYTON_DEFAULT_LIMITED_ACCESS_TYPE
		)
	if limited:
		section["limitedAccessType"] = limited

	return section
