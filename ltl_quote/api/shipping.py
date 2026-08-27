"""Dayton Shipping catalog sync helpers (packaging types, shipping classes, etc.)."""

from __future__ import annotations

import frappe
import requests

from ltl_quote.carrier_network.adapters.dayton import DaytonCarrierAdapter, REQUEST_TIMEOUT

DAYTON_PACKAGING_TYPES_PATH = "/api/Shipping/PackagingTypes"
DAYTON_SHIPPING_CLASSES_PATH = "/api/Shipping/classes"
DAYTON_ACCESSORIALS_PATH = "/api/Shipping/Accessorials"
DAYTON_RESPONSE_ACCESSORIALS_PATH = "/api/Shipping/ResponseAccessorials"
DAYTON_STATES_PROVINCES_PATH = "/api/Shipping/StatesProvinces"
DAYTON_LTL_ACCESSORIALS_PATH = "/api/Shipping/LTLAccessorials"
DAYTON_SERVICE_CENTERS_PATH = "/api/ServiceCenters"

# Fallback when Dayton is unreachable or catalog is empty (matches Dayton sample response).
DEFAULT_SHIPPING_CLASSES = [
	"50",
	"55",
	"60",
	"65",
	"70",
	"77.5",
	"85",
	"92.5",
	"100",
	"110",
	"125",
	"150",
	"175",
	"200",
	"250",
	"300",
	"400",
	"500",
]


def _normalize_class_code(value) -> str | None:
	"""Normalize Dayton class numbers (50, 77.5) to stable string codes."""
	if value is None or value == "":
		return None
	try:
		num = float(value)
	except (TypeError, ValueError):
		text = str(value).strip()
		return text or None
	if num == int(num):
		return str(int(num))
	text = f"{num:.4f}".rstrip("0").rstrip(".")
	return text or None


def ensure_shipping_class(code) -> str:
	"""Return a Dayton Shipping Class name, seeding the row if the catalog is missing it.

	LTL Quote Request.freight_class is a Link. Rating APIs must not fail when the
	sandbox/site never synced Dayton classes, or when the stored name is 70.0 vs 70.
	"""
	normalized = _normalize_class_code(code) or str(code or "").strip()
	if not normalized:
		return str(code or "")

	if not frappe.db.count("Dayton Shipping Class"):
		for default_code in DEFAULT_SHIPPING_CLASSES:
			if frappe.db.exists("Dayton Shipping Class", default_code):
				continue
			frappe.get_doc(
				{
					"doctype": "Dayton Shipping Class",
					"class_code": default_code,
					"description": f"Freight Class {default_code}",
				}
			).insert(ignore_permissions=True)

	for candidate in (normalized, f"{normalized}.0"):
		existing = frappe.db.exists("Dayton Shipping Class", candidate)
		if existing:
			return existing

	doc = frappe.get_doc(
		{
			"doctype": "Dayton Shipping Class",
			"class_code": normalized,
			"description": f"Freight Class {normalized}",
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


@frappe.whitelist(allow_guest=False)
def sync_packaging_types():
	"""
	Fetch Dayton GET /api/Shipping/PackagingTypes and upsert into Dayton Packaging Type.

	Uses LTL Carrier DAYTON credentials via DaytonCarrierAdapter (no hardcoded secrets).
	"""
	adapter = DaytonCarrierAdapter()
	url = f"{adapter.base_url}{DAYTON_PACKAGING_TYPES_PATH}"

	try:
		response = requests.get(
			url,
			headers=adapter.get_headers(),
			auth=adapter.get_auth(),
			timeout=REQUEST_TIMEOUT,
		)
		response.raise_for_status()
		data = response.json() if response.content else {}
	except requests.exceptions.RequestException as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Dayton Packaging Sync Failure",
		)
		frappe.throw(f"API Connection Failure: {e}")

	packaging_types = data.get("packagingTypes") or data.get("PackagingTypes") or []
	if isinstance(data, list):
		packaging_types = data

	synced_count = 0
	for p_type in packaging_types:
		if not isinstance(p_type, dict):
			continue
		code = str(p_type.get("id") or p_type.get("code") or "").strip()
		name = str(p_type.get("name") or p_type.get("description") or "").strip()
		if not code or not name:
			continue

		if frappe.db.exists("Dayton Packaging Type", code):
			frappe.db.set_value("Dayton Packaging Type", code, "description", name)
		else:
			doc = frappe.get_doc(
				{
					"doctype": "Dayton Packaging Type",
					"id": code,
					"description": name,
				}
			)
			doc.insert(ignore_permissions=True)
		synced_count += 1

	frappe.db.commit()
	return {
		"status": "success",
		"synced_count": synced_count,
		"message": f"Successfully synced {synced_count} packaging formats.",
	}


@frappe.whitelist(allow_guest=False)
def get_packaging_type_options():
	"""Return synced packaging types for the LTL Quote line-item editor."""
	rows = frappe.get_all(
		"Dayton Packaging Type",
		fields=["name", "id", "description"],
		order_by="id asc",
	)
	return [
		{
			"value": row.id or row.name,
			"label": f"{row.id or row.name} — {row.description}" if row.description else (row.id or row.name),
		}
		for row in rows
	]


@frappe.whitelist(allow_guest=False)
def sync_shipping_classes():
	"""
	Fetch Dayton GET /api/Shipping/classes and upsert into Dayton Shipping Class.

	Uses LTL Carrier DAYTON credentials via DaytonCarrierAdapter.
	"""
	adapter = DaytonCarrierAdapter()
	url = f"{adapter.base_url}{DAYTON_SHIPPING_CLASSES_PATH}"
	classes: list = []

	try:
		response = requests.get(
			url,
			headers=adapter.get_headers(),
			auth=adapter.get_auth(),
			timeout=REQUEST_TIMEOUT,
		)
		response.raise_for_status()
		data = response.json() if response.content else {}
		if isinstance(data, dict):
			classes = data.get("classes") or data.get("Classes") or []
		elif isinstance(data, list):
			classes = data
	except requests.exceptions.RequestException as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Dayton Shipping Classes Sync Failure",
		)
		# Soft-fallback so UI still has options if carrier is temporarily down.
		classes = list(DEFAULT_SHIPPING_CLASSES)
		frappe.msgprint(
			f"Dayton classes API unavailable ({e}); seeded default NMFC classes.",
			indicator="orange",
			alert=True,
		)

	synced_count = 0
	seen: set[str] = set()
	for raw in classes or []:
		code = _normalize_class_code(raw)
		if not code or code in seen:
			continue
		seen.add(code)
		description = f"Freight Class {code}"
		if frappe.db.exists("Dayton Shipping Class", code):
			frappe.db.set_value("Dayton Shipping Class", code, "description", description)
		else:
			doc = frappe.get_doc(
				{
					"doctype": "Dayton Shipping Class",
					"class_code": code,
					"description": description,
				}
			)
			doc.insert(ignore_permissions=True)
		synced_count += 1

	frappe.db.commit()
	return {
		"status": "success",
		"synced_count": synced_count,
		"message": f"Successfully synced {synced_count} shipping classes.",
	}


@frappe.whitelist(allow_guest=False)
def get_shipping_class_options():
	"""Return synced shipping classes for LTL Quote freight / NMFC class dropdowns."""
	rows = frappe.get_all(
		"Dayton Shipping Class",
		fields=["name", "class_code", "description"],
		order_by="class_code asc",
	)
	if not rows:
		return [{"value": c, "label": c} for c in DEFAULT_SHIPPING_CLASSES]

	def _sort_key(row):
		try:
			return float(row.class_code or row.name)
		except (TypeError, ValueError):
			return 9999.0

	rows = sorted(rows, key=_sort_key)
	return [
		{
			"value": row.class_code or row.name,
			"label": row.class_code or row.name,
		}
		for row in rows
	]


def _dayton_get(path: str, title: str):
	"""Authenticated GET against Dayton Shipping APIs. Returns parsed JSON or throws."""
	adapter = DaytonCarrierAdapter()
	url = f"{adapter.base_url}{path}"
	try:
		response = requests.get(
			url,
			headers=adapter.get_headers(),
			auth=adapter.get_auth(),
			timeout=REQUEST_TIMEOUT,
		)
		response.raise_for_status()
		return response.json() if response.content else {}
	except requests.exceptions.RequestException as e:
		frappe.log_error(message=frappe.get_traceback(), title=title)
		frappe.throw(f"API Connection Failure: {e}")


@frappe.whitelist(allow_guest=False)
def sync_dayton_accessorials():
	"""
	Fetch Dayton GET /api/Shipping/Accessorials and upsert into Dayton Accessorial.

	Stores all service groups (Pickup Services, Delivery Services, etc.).
	Codes may repeat across descriptions, so rows are keyed by code+description+group.
	"""
	data = _dayton_get(DAYTON_ACCESSORIALS_PATH, "Dayton Accessorials Sync Failure")
	groups = data.get("accessorials") or data.get("Accessorials") or {}
	if isinstance(data, list):
		# Unexpected flat list — treat as ungrouped.
		groups = {"Other": data}

	synced_count = 0
	for group_name, rows in (groups or {}).items():
		service_group = str(group_name or "").strip()
		for row in rows or []:
			if not isinstance(row, dict):
				continue
			code = str(row.get("code") or row.get("id") or "").strip()
			description = str(row.get("description") or row.get("name") or "").strip()
			if not code or not description:
				continue

			filters = {
				"code": code,
				"description": description,
				"service_group": service_group,
			}
			existing = frappe.db.get_value("Dayton Accessorial", filters, "name")
			if existing:
				# Already present; nothing to update beyond identity fields.
				synced_count += 1
				continue

			frappe.get_doc(
				{
					"doctype": "Dayton Accessorial",
					"code": code,
					"description": description,
					"service_group": service_group,
				}
			).insert(ignore_permissions=True)
			synced_count += 1

	frappe.db.commit()
	return {
		"status": "success",
		"synced_count": synced_count,
		"message": f"Successfully synced {synced_count} Dayton accessorials.",
	}


# When many Dayton rows share one code (e.g. LIMITP), prefer these generic labels.
_GENERIC_ACCESSORIAL_DESCRIPTIONS = {
	"LIMIT": "Limited Access",
	"LIMITP": "Limited Access",
}


def _prefer_accessorial_description(code: str, candidates: list[str]) -> str:
	"""Pick the best display description when multiple rows share a code."""
	cleaned = [str(c).strip() for c in candidates if str(c or "").strip()]
	if not cleaned:
		return code
	preferred = _GENERIC_ACCESSORIAL_DESCRIPTIONS.get(str(code or "").strip().upper())
	if preferred:
		for desc in cleaned:
			if desc.lower() == preferred.lower():
				return desc
	# Prefer shorter, simpler labels over long limited-access subtypes.
	return sorted(cleaned, key=lambda d: (len(d), d.lower()))[0]


@frappe.whitelist(allow_guest=False)
def get_dayton_accessorial_options(group=None, unique_codes=0):
	"""Return synced Dayton accessorials, optionally filtered by service_group substring.

	When ``unique_codes`` is truthy, collapse duplicate codes (e.g. many LIMITP rows)
	to one option each — suitable for quote-form pickers.
	"""
	filters = {}
	group_text = str(group or "").strip()
	if group_text:
		filters["service_group"] = ["like", f"%{group_text}%"]

	rows = frappe.get_all(
		"Dayton Accessorial",
		fields=["name", "code", "description", "service_group"],
		filters=filters,
		order_by="service_group asc, code asc",
	)

	if not frappe.utils.cint(unique_codes):
		return [
			{
				"value": row.code,
				"label": (
					f"{row.code} — {row.description}"
					+ (f" ({row.service_group})" if row.service_group else "")
				),
				"code": row.code,
				"description": row.description,
				"service_group": row.service_group,
			}
			for row in rows
		]

	by_code: dict[str, dict] = {}
	for row in rows:
		code = str(row.code or "").strip()
		if not code:
			continue
		entry = by_code.setdefault(
			code,
			{"code": code, "descriptions": [], "service_group": row.service_group or ""},
		)
		if row.description:
			entry["descriptions"].append(row.description)
		if not entry["service_group"] and row.service_group:
			entry["service_group"] = row.service_group

	options = []
	for code in sorted(by_code.keys()):
		entry = by_code[code]
		description = _prefer_accessorial_description(code, entry["descriptions"])
		options.append(
			{
				"value": code,
				"label": f"{code} — {description}",
				"code": code,
				"description": description,
				"service_group": entry["service_group"],
			}
		)
	return options


@frappe.whitelist(allow_guest=False)
def sync_response_accessorials():
	"""
	Fetch Dayton GET /api/Shipping/ResponseAccessorials and upsert into
	Dayton Response Accessorial (unique by code).
	"""
	data = _dayton_get(
		DAYTON_RESPONSE_ACCESSORIALS_PATH, "Dayton Response Accessorials Sync Failure"
	)
	items = (
		data.get("responseAccessorials")
		or data.get("ResponseAccessorials")
		or data.get("accessorials")
		or []
	)
	if isinstance(data, list):
		items = data

	synced_count = 0
	for row in items or []:
		if not isinstance(row, dict):
			continue
		code = str(row.get("code") or row.get("id") or "").strip()
		description = str(row.get("description") or row.get("name") or "").strip()
		if not code:
			continue
		if not description:
			description = code

		if frappe.db.exists("Dayton Response Accessorial", code):
			frappe.db.set_value("Dayton Response Accessorial", code, "description", description)
		else:
			frappe.get_doc(
				{
					"doctype": "Dayton Response Accessorial",
					"code": code,
					"description": description,
				}
			).insert(ignore_permissions=True)
		synced_count += 1

	frappe.db.commit()
	return {
		"status": "success",
		"synced_count": synced_count,
		"message": f"Successfully synced {synced_count} response accessorials.",
	}


@frappe.whitelist(allow_guest=False)
def get_response_accessorial_options():
	"""Return synced Dayton response accessorials for Desk / enrichment lookups."""
	rows = frappe.get_all(
		"Dayton Response Accessorial",
		fields=["name", "code", "description"],
		order_by="code asc",
	)
	return [
		{
			"value": row.code or row.name,
			"label": f"{row.code} — {row.description}" if row.description else (row.code or row.name),
			"code": row.code or row.name,
			"description": row.description,
		}
		for row in rows
	]


@frappe.whitelist(allow_guest=False)
def get_service_eligibility(origin=None, destination=None, date=None):
	"""Lookup Dayton lane service eligibility (transit days + service centers)."""
	from ltl_quote.carrier_network.adapters.dayton import DaytonCarrierAdapter

	adapter = DaytonCarrierAdapter()
	result = adapter.get_service_eligibility(origin, destination, date)
	if not result:
		return {
			"status": "error",
			"message": "Service eligibility lookup failed or returned no data.",
		}
	return {
		"status": "success",
		"data": result,
		**result,
	}


@frappe.whitelist(allow_guest=False)
def sync_states_provinces():
	"""
	Fetch Dayton GET /api/Shipping/StatesProvinces and upsert into Dayton State Province.
	"""
	data = _dayton_get(DAYTON_STATES_PROVINCES_PATH, "Dayton States/Provinces Sync Failure")
	items = (
		data.get("supportedStatesProvinces")
		or data.get("SupportedStatesProvinces")
		or data.get("statesProvinces")
		or []
	)
	if isinstance(data, list):
		items = data

	synced_count = 0
	seen: set[str] = set()
	for raw in items or []:
		if isinstance(raw, dict):
			code = str(raw.get("code") or raw.get("id") or raw.get("name") or "").strip().upper()
			description = str(raw.get("description") or raw.get("name") or code).strip()
		else:
			code = str(raw or "").strip().upper()
			description = code
		if not code or code in seen:
			continue
		seen.add(code)

		if frappe.db.exists("Dayton State Province", code):
			frappe.db.set_value("Dayton State Province", code, "description", description)
		else:
			frappe.get_doc(
				{
					"doctype": "Dayton State Province",
					"code": code,
					"description": description,
				}
			).insert(ignore_permissions=True)
		synced_count += 1

	frappe.db.commit()
	return {
		"status": "success",
		"synced_count": synced_count,
		"message": f"Successfully synced {synced_count} states/provinces.",
	}


@frappe.whitelist(allow_guest=False)
def sync_service_centers():
	"""Fetch Dayton GET /api/ServiceCenters and upsert into Dayton Service Center."""
	from ltl_quote.carrier_network.service_center_sync import sync_dayton_service_centers

	return sync_dayton_service_centers()


@frappe.whitelist(allow_guest=False)
def get_state_province_options():
	"""Return synced Dayton states/provinces for quote Origin/Destination State selects."""
	rows = frappe.get_all(
		"Dayton State Province",
		fields=["name", "code", "description"],
		order_by="code asc",
	)
	return [
		{
			"value": row.code or row.name,
			"label": row.code or row.name,
			"description": row.description,
		}
		for row in rows
	]


def _ltl_bol_accessorial_description(code: str) -> str:
	"""Best-effort label for a Dayton LTL Digital Standard BOL accessorial code."""
	from ltl_quote.carrier_network.accessorials import DAYTON_BOL_CODE_LABELS

	code = str(code or "").strip().upper()
	if not code:
		return ""
	if code in DAYTON_BOL_CODE_LABELS:
		return DAYTON_BOL_CODE_LABELS[code]
	if frappe.db.exists("Dayton Response Accessorial", code):
		return frappe.db.get_value("Dayton Response Accessorial", code, "description") or code
	return code


@frappe.whitelist(allow_guest=False)
def sync_ltl_accessorials():
	"""
	Fetch Dayton GET /api/Shipping/LTLAccessorials and upsert into Dayton LTL Accessorial.

	These are NMFTA / LTL Digital Standard codes supported on the eBOL endpoint.
	"""
	data = _dayton_get(DAYTON_LTL_ACCESSORIALS_PATH, "Dayton LTL Accessorials Sync Failure")
	items = (
		data.get("supportedAccessorials")
		or data.get("SupportedAccessorials")
		or data.get("accessorials")
		or []
	)
	if isinstance(data, list):
		items = data

	synced_count = 0
	seen: set[str] = set()
	for raw in items or []:
		if isinstance(raw, dict):
			code = str(raw.get("code") or raw.get("id") or raw.get("name") or "").strip().upper()
		else:
			code = str(raw or "").strip().upper()
		if not code or code in seen:
			continue
		seen.add(code)
		description = _ltl_bol_accessorial_description(code)

		if frappe.db.exists("Dayton LTL Accessorial", code):
			frappe.db.set_value("Dayton LTL Accessorial", code, "description", description)
		else:
			frappe.get_doc(
				{
					"doctype": "Dayton LTL Accessorial",
					"code": code,
					"description": description,
				}
			).insert(ignore_permissions=True)
		synced_count += 1

	frappe.db.commit()
	return {
		"status": "success",
		"synced_count": synced_count,
		"message": f"Successfully synced {synced_count} LTL BOL accessorials.",
	}


@frappe.whitelist(allow_guest=False)
def get_ltl_accessorial_options():
	"""Return synced Dayton LTL Digital Standard BOL accessorial codes."""
	rows = frappe.get_all(
		"Dayton LTL Accessorial",
		fields=["name", "code", "description"],
		order_by="code asc",
	)
	return [
		{
			"value": row.code or row.name,
			"label": (
				f"{row.code} — {row.description}" if row.description and row.description != row.code else row.code
			),
			"code": row.code or row.name,
			"description": row.description,
		}
		for row in rows
	]


@frappe.whitelist(allow_guest=False)
def search_dayton_images(pro=None):
	"""Query Dayton GET /api/Images/Search for indexed documents on a PRO."""
	from ltl_quote.carrier_network.adapters.dayton import search_dayton_images as _search_dayton_images

	if not pro:
		frappe.throw("pro is required.")
	result = _search_dayton_images(pro)
	return {"status": "success" if result.get("success") else "error", **result}


@frappe.whitelist(allow_guest=False)
def dayton_document_available(pro=None, doc_type="BILL OF LADING"):
	"""Check whether a Dayton document type is indexed before downloading."""
	from ltl_quote.carrier_network.adapters.dayton import dayton_document_available as _dayton_document_available

	return _dayton_document_available(pro, doc_type)


@frappe.whitelist(allow_guest=False)
def refresh_dayton_shipment_bol(shipment=None, shipment_name=None):
	"""Download and attach a missing Dayton BOL when Images/Search reports it indexed."""
	from ltl_quote.carrier_network.adapters.dayton import (
		_fetch_remote_bol_for_shipment,
		_is_dayton_shipment,
	)

	name = str(shipment or shipment_name or "").strip()
	if not name or not frappe.db.exists("LTL Shipment", name):
		frappe.throw("A valid shipment ID is required.")

	doc = frappe.get_doc("LTL Shipment", name)
	frappe.has_permission("LTL Shipment", "write", doc=doc, throw=True)
	if not _is_dayton_shipment(doc):
		frappe.throw("BOL refresh is only available for Dayton Freight shipments.")

	result = _fetch_remote_bol_for_shipment(doc)
	if result.get("success"):
		return {"status": "success", **result}
	return {"status": result.get("status") or "error", **result}


def _get_dayton_shipment(shipment=None, shipment_name=None):
	from ltl_quote.carrier_network.carrier_identity import CONNECTOR_DAYTON, shipment_connector

	name = str(shipment or shipment_name or "").strip()
	if not name or not frappe.db.exists("LTL Shipment", name):
		frappe.throw("A valid shipment ID is required.")
	doc = frappe.get_doc("LTL Shipment", name)
	frappe.has_permission("LTL Shipment", "read", doc=doc, throw=True)
	if shipment_connector(doc) != CONNECTOR_DAYTON:
		frappe.throw("Pickup APIs are only available for Dayton Freight shipments.")
	return doc


@frappe.whitelist(allow_guest=False)
def create_dayton_pickup(shipment=None, shipment_name=None):
	"""Schedule a Dayton pickup for a booked shipment (PUT /api/Pickup)."""
	from ltl_quote.carrier_network.adapters.dayton import DaytonCarrierAdapter
	from ltl_quote.carrier_network.pickup import shipment_pickup_summary

	doc = _get_dayton_shipment(shipment, shipment_name)
	frappe.has_permission("LTL Shipment", "write", doc=doc, throw=True)
	adapter = DaytonCarrierAdapter()
	result = adapter.create_pickup(doc)
	doc.reload()
	return {
		"status": "success",
		"shipment": doc.name,
		"pickup": shipment_pickup_summary(doc),
		**result,
	}


@frappe.whitelist(allow_guest=False)
def get_dayton_pickup(shipment=None, shipment_name=None, number=None):
	"""Fetch Dayton pickup details (GET /api/Pickup)."""
	from ltl_quote.carrier_network.adapters.dayton import DaytonCarrierAdapter
	from ltl_quote.carrier_network.pickup import apply_pickup_response_to_shipment, shipment_pickup_summary

	adapter = DaytonCarrierAdapter()
	pickup_number = str(number or "").strip()
	if shipment or shipment_name:
		doc = _get_dayton_shipment(shipment, shipment_name)
		pickup_number = pickup_number or doc.pickup_number
		if not pickup_number:
			frappe.throw("This shipment does not have a pickup number yet.")
		result = adapter.get_pickup(pickup_number)
		if result.get("ok"):
			apply_pickup_response_to_shipment(doc, result, save=True)
			doc.reload()
			return {"status": "success", "shipment": doc.name, "pickup": shipment_pickup_summary(doc, live=False), **result}
		return {"status": "error", "shipment": doc.name, **result}

	if not pickup_number:
		frappe.throw("Provide either shipment or number.")
	result = adapter.get_pickup(pickup_number)
	return {"status": "success" if result.get("ok") else "error", **result}


@frappe.whitelist(allow_guest=False)
def update_dayton_pickup(shipment=None, shipment_name=None, payload=None):
	"""Update a Dayton pickup (POST /api/Pickup)."""
	from ltl_quote.carrier_network.adapters.dayton import DaytonCarrierAdapter
	from ltl_quote.carrier_network.pickup import apply_pickup_response_to_shipment, shipment_pickup_summary

	if isinstance(payload, str):
		payload = frappe.parse_json(payload)
	payload = payload or {}

	doc = _get_dayton_shipment(shipment, shipment_name)
	frappe.has_permission("LTL Shipment", "write", doc=doc, throw=True)
	if not doc.pickup_number:
		frappe.throw("This shipment does not have a pickup number to update.")

	from ltl_quote.carrier_network.pickup import format_pickup_datetime

	body = dict(payload)
	if body.get("ready"):
		body["ready"] = format_pickup_datetime(body["ready"])
	if body.get("close"):
		body["close"] = format_pickup_datetime(body["close"])
	if body.get("pickup_ready"):
		doc.pickup_ready = body.pop("pickup_ready")
	if body.get("pickup_close"):
		doc.pickup_close = body.pop("pickup_close")
	if body.get("ready"):
		doc.pickup_ready = body["ready"]
	if body.get("close"):
		doc.pickup_close = body["close"]
	doc.save(ignore_permissions=True)

	adapter = DaytonCarrierAdapter()
	result = adapter.update_pickup(doc.pickup_number, body)
	apply_pickup_response_to_shipment(doc, result, save=True)
	doc.reload()
	return {"status": "success", "shipment": doc.name, "pickup": shipment_pickup_summary(doc), **result}


@frappe.whitelist(allow_guest=False)
def update_dayton_pickup_by_psid(shipment=None, shipment_name=None, payload=None, psid=None):
	"""Update a Dayton pickup line by PSID (POST /api/Pickup/ByPSID)."""
	from ltl_quote.carrier_network.adapters.dayton import DaytonCarrierAdapter
	from ltl_quote.carrier_network.pickup import apply_pickup_response_to_shipment, shipment_pickup_summary

	if isinstance(payload, str):
		payload = frappe.parse_json(payload)
	payload = payload or {}

	doc = _get_dayton_shipment(shipment, shipment_name)
	frappe.has_permission("LTL Shipment", "write", doc=doc, throw=True)
	target_psid = psid or doc.pickup_psid or payload.get("psid")
	if not target_psid:
		frappe.throw("A pickup shipment ID (PSID) is required.")

	adapter = DaytonCarrierAdapter()
	result = adapter.update_pickup_by_psid(target_psid, payload)
	apply_pickup_response_to_shipment(doc, result, save=True)
	doc.reload()
	return {"status": "success", "shipment": doc.name, "pickup": shipment_pickup_summary(doc), **result}


@frappe.whitelist(allow_guest=False)
def cancel_dayton_pickup(shipment=None, shipment_name=None, number=None):
	"""Cancel a Dayton pickup (DELETE /api/Pickup/Cancel)."""
	from ltl_quote.carrier_network.adapters.dayton import DaytonCarrierAdapter
	from ltl_quote.carrier_network.pickup import resolve_pickup_cancel_number, shipment_pickup_summary

	doc = None
	if shipment or shipment_name:
		doc = _get_dayton_shipment(shipment, shipment_name)
		frappe.has_permission("LTL Shipment", "write", doc=doc, throw=True)
		target = resolve_pickup_cancel_number(doc)
	else:
		target = str(number or "").strip()
		if not target:
			frappe.throw("Provide either shipment or number.")

	adapter = DaytonCarrierAdapter()
	result = adapter.cancel_pickup(target)
	if doc and result.get("success"):
		doc.pickup_status = "Cancelled"
		doc.dispatch_status = "Failed"
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		doc.reload()
		return {"status": "success", "shipment": doc.name, "pickup": shipment_pickup_summary(doc), **result}
	return {"status": "success" if result.get("success") else "error", **result}


def _get_tforce_shipment(shipment=None, shipment_name=None):
	from ltl_quote.carrier_network.carrier_identity import CONNECTOR_TFORCE, shipment_connector

	name = str(shipment or shipment_name or "").strip()
	if not name or not frappe.db.exists("LTL Shipment", name):
		frappe.throw("A valid shipment ID is required.")
	doc = frappe.get_doc("LTL Shipment", name)
	frappe.has_permission("LTL Shipment", "read", doc=doc, throw=True)
	if shipment_connector(doc) != CONNECTOR_TFORCE:
		frappe.throw("TForce pickup APIs are only available for TForce Freight shipments.")
	return doc


@frappe.whitelist(allow_guest=False)
def create_tforce_pickup(shipment=None, shipment_name=None):
	"""Schedule a TForce pickup for a booked shipment (POST /pickup/request)."""
	from ltl_quote.carrier_network.adapters.tforce import TForceCarrierAdapter
	from ltl_quote.carrier_network.pickup import shipment_pickup_summary

	doc = _get_tforce_shipment(shipment, shipment_name)
	frappe.has_permission("LTL Shipment", "write", doc=doc, throw=True)
	adapter = TForceCarrierAdapter(frappe.get_doc("LTL Carrier", doc.carrier))
	result = adapter.create_pickup(doc)
	doc.reload()
	return {
		"status": "success",
		"shipment": doc.name,
		"pickup": shipment_pickup_summary(doc),
		**result,
	}


@frappe.whitelist(allow_guest=False)
def get_tforce_pickup(shipment=None, shipment_name=None, number=None):
	"""Return stored TForce pickup confirmation (TForce has no pickup GET)."""
	from ltl_quote.carrier_network.pickup import shipment_pickup_summary

	doc = _get_tforce_shipment(shipment, shipment_name)
	pickup_number = str(number or doc.pickup_number or "").strip()
	if not pickup_number:
		frappe.throw("This shipment does not have a TForce pickup confirmation yet.")
	summary = shipment_pickup_summary(doc)
	return {
		"status": "success",
		"ok": True,
		"shipment": doc.name,
		"pickup": summary,
		"pickup_number": pickup_number,
		"pickup_status": doc.pickup_status or "Scheduled",
	}


@frappe.whitelist(allow_guest=False)
def cancel_tforce_pickup(shipment=None, shipment_name=None, number=None):
	"""Cancel a TForce pickup (DELETE /pickup/request/{confirmationNumber})."""
	from ltl_quote.carrier_network.adapters.tforce import TForceCarrierAdapter
	from ltl_quote.carrier_network.pickup import resolve_pickup_cancel_number, shipment_pickup_summary

	doc = None
	if shipment or shipment_name:
		doc = _get_tforce_shipment(shipment, shipment_name)
		frappe.has_permission("LTL Shipment", "write", doc=doc, throw=True)
		target = str(number or "").strip() or resolve_pickup_cancel_number(doc)
	else:
		target = str(number or "").strip()
		if not target:
			frappe.throw("Provide either shipment or number.")

	adapter = TForceCarrierAdapter(frappe.get_doc("LTL Carrier", doc.carrier) if doc else None)
	result = adapter.cancel_pickup(target)
	if doc and result.get("success"):
		doc.pickup_status = "Cancelled"
		doc.dispatch_status = "Failed"
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		doc.reload()
		return {"status": "success", "shipment": doc.name, "pickup": shipment_pickup_summary(doc), **result}
	return {"status": "success" if result.get("success") else "error", **result}


def _get_arcbest_shipment(shipment=None, shipment_name=None):
	from ltl_quote.carrier_network.carrier_identity import CONNECTOR_ARCBEST, shipment_connector

	name = str(shipment or shipment_name or "").strip()
	if not name or not frappe.db.exists("LTL Shipment", name):
		frappe.throw("A valid shipment ID is required.")
	doc = frappe.get_doc("LTL Shipment", name)
	frappe.has_permission("LTL Shipment", "read", doc=doc, throw=True)
	if shipment_connector(doc) != CONNECTOR_ARCBEST:
		frappe.throw("ArcBest pickup APIs are only available for ArcBest shipments.")
	return doc


@frappe.whitelist(allow_guest=False)
def create_arcbest_pickup(shipment=None, shipment_name=None):
	"""Record ArcBest pickup from the booked BOL ship date (no separate pickup API)."""
	from ltl_quote.carrier_network.adapters.arcbest import ArcBestCarrierAdapter
	from ltl_quote.carrier_network.pickup import shipment_pickup_summary

	doc = _get_arcbest_shipment(shipment, shipment_name)
	frappe.has_permission("LTL Shipment", "write", doc=doc, throw=True)
	adapter = ArcBestCarrierAdapter(frappe.get_doc("LTL Carrier", doc.carrier))
	result = adapter.create_pickup(doc)
	doc.reload()
	return {
		"status": "success",
		"shipment": doc.name,
		"pickup": shipment_pickup_summary(doc),
		**result,
	}


@frappe.whitelist(allow_guest=False)
def get_arcbest_pickup(shipment=None, shipment_name=None, number=None):
	"""Return stored ArcBest pickup fields (BOL ship date / pickup number)."""
	from ltl_quote.carrier_network.pickup import shipment_pickup_summary

	doc = _get_arcbest_shipment(shipment, shipment_name)
	pickup_number = str(number or doc.pickup_number or doc.bol_number or doc.pro_number or "").strip()
	if not pickup_number:
		frappe.throw("This shipment does not have an ArcBest pickup reference yet.")
	summary = shipment_pickup_summary(doc)
	if not summary.get("pickup_number"):
		summary["pickup_number"] = pickup_number
	return {
		"status": "success",
		"ok": True,
		"shipment": doc.name,
		"pickup": summary,
		"pickup_number": pickup_number,
		"pickup_status": doc.pickup_status or "Scheduled",
	}


@frappe.whitelist(allow_guest=False)
def cancel_arcbest_pickup(shipment=None, shipment_name=None, number=None):
	"""Cancel a locally recorded ArcBest pickup (no carrier cancel API)."""
	from ltl_quote.carrier_network.pickup import shipment_pickup_summary

	doc = _get_arcbest_shipment(shipment, shipment_name)
	frappe.has_permission("LTL Shipment", "write", doc=doc, throw=True)
	doc.pickup_status = "Cancelled"
	doc.dispatch_status = "Failed"
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	doc.reload()
	return {
		"status": "success",
		"success": True,
		"shipment": doc.name,
		"pickup": shipment_pickup_summary(doc),
		"message": "ArcBest pickup marked cancelled locally. Contact ArcBest to change a tendered pickup.",
	}
