# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""SMC3 Bill of Lading v1 request builder and response helpers."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta

from frappe.utils import cint, flt

DEFAULT_BOL_BASE = "https://bill-of-lading.smc3.com/bill-of-lading/v1/app"
DEFAULT_BOL_VERSION = "2.1.0"
DEFAULT_SANDBOX_ACCOUNT = "12345"
DEFAULT_DOCUMENT_DEMO_PRO = "11234559"

HANDLING_UNIT_TYPES = {
	"SKD": "SKD",
	"SKID": "SKD",
	"PALLET": "SKD",
	"PLT": "SKD",
	"PAT": "SKD",
	"CTN": "CTN",
	"CARTON": "CTN",
	"BOX": "BOX",
	"DRM": "DRM",
	"DRUM": "DRM",
	"PCS": "PCS",
	"PIECE": "PCS",
	"PIECES": "PCS",
}

LINE_PACKAGING_TYPES = {
	"BOX": "BOX",
	"CTN": "CTN",
	"CARTON": "CTN",
	"DRM": "DRM",
	"DRUM": "DRM",
	"SKD": "SKD",
	"SKID": "SKD",
	"PLT": "PLT",
	"PALLET": "PLT",
	"PAT": "PLT",
	"PCS": "PCS",
}


def build_bol_payload(quote_data: dict, *, is_test: bool, account: str, function: str = "Create") -> dict:
	"""Map a platform booking payload onto the SMC3 BOL Create/Update body."""
	quote_data = quote_data or {}
	origin = _party(
		quote_data,
		account=account,
		name=quote_data.get("shipper_name") or quote_data.get("shipper_company_name") or "Shipper Co",
		address=quote_data.get("shipper_address"),
		city=quote_data.get("origin_city"),
		state=quote_data.get("origin_state"),
		postal=quote_data.get("origin_zip"),
		country=quote_data.get("origin_country"),
		contact_name=quote_data.get("origin_contact_name") or quote_data.get("contact_name"),
		contact_phone=quote_data.get("origin_contact_phone") or quote_data.get("contact_phone"),
	)
	destination = _party(
		quote_data,
		account=account,
		name=quote_data.get("consignee_name") or quote_data.get("consignee_company_name") or "Consignee Co",
		address=quote_data.get("consignee_address"),
		city=quote_data.get("destination_city"),
		state=quote_data.get("destination_state"),
		postal=quote_data.get("destination_zip"),
		country=quote_data.get("destination_country"),
		contact_name=quote_data.get("destination_contact_name"),
		contact_phone=quote_data.get("destination_contact_phone"),
	)
	bill_to = _party(
		quote_data,
		account=account,
		name=quote_data.get("bill_to_name") or origin["name"] or "Bill To Co",
		address=quote_data.get("bill_to_address") or origin.get("address1"),
		city=quote_data.get("bill_to_city") or origin.get("city"),
		state=quote_data.get("bill_to_state") or origin.get("stateProvince"),
		postal=quote_data.get("bill_to_zip") or origin.get("postalCode"),
		country=quote_data.get("bill_to_country") or origin.get("country"),
		contact_name=quote_data.get("bill_to_contact_name") or origin.get("contact", {}).get("name"),
		contact_phone=quote_data.get("bill_to_contact_phone") or origin.get("contact", {}).get("phone"),
	)
	payload = {
		"bol": {
			"requestedPickupDate": _pickup_datetime(quote_data),
			"function": str(function or quote_data.get("bol_function") or "Create"),
			"isTest": bool(is_test),
			"requestorRole": str(quote_data.get("requestor_role") or "Third Party"),
		},
		"version": str(quote_data.get("bol_version") or DEFAULT_BOL_VERSION),
		"payment": {"terms": str(quote_data.get("payment_terms") or "Prepaid")},
		"commodities": {
			"lineItemLayout": "Nested",
			"handlingUnits": _handling_units(quote_data),
		},
		"origin": origin,
		"destination": destination,
		"billTo": bill_to,
	}
	pro = str(quote_data.get("pro_number") or "").strip()
	if pro:
		payload["referenceNumbers"] = {"pro": pro}
	return payload


def extract_bol_pdf(data: dict) -> str:
	images = data.get("images") if isinstance(data, dict) else None
	if not isinstance(images, dict):
		return ""
	raw = images.get("bol") or images.get("billOfLading") or images.get("bolDocument") or ""
	return str(raw or "").strip()


def extract_bol_png_images(data: dict) -> list[str]:
	"""Return Base64 PNG pages from the SMC3 Document API `images` array."""
	images = data.get("images") if isinstance(data, dict) else None
	if isinstance(images, list):
		raw_pages = [str(item).strip() for item in images if str(item or "").strip()]
	elif isinstance(images, dict):
		raw = images.get("bol") or images.get("billOfLading") or images.get("bolDocument") or ""
		raw_pages = [str(raw).strip()] if str(raw or "").strip() else []
	elif isinstance(images, str) and images.strip():
		raw_pages = [images.strip()]
	else:
		raw_pages = []
	return [page for page in raw_pages if is_usable_png_image(page)]


def is_usable_png_image(raw: str) -> bool:
	"""True when the payload looks like a Base64 PNG (magic `iVBORw0KGgo`)."""
	text = str(raw or "").strip()
	if "," in text and text.lower().startswith("data:"):
		text = text.split(",", 1)[1]
	text = "".join(text.split())
	return text.startswith("iVBORw0KGgo")


def extract_reference_numbers(data: dict) -> dict:
	refs = data.get("referenceNumbers") if isinstance(data, dict) else None
	if not isinstance(refs, dict):
		refs = {}
	return {
		"pro": str(refs.get("pro") or refs.get("proNumber") or "").strip(),
		"shipment_confirmation": str(
			refs.get("shipmentConfirmationNumber") or refs.get("bolNumber") or ""
		).strip(),
	}


def sanitize_bol_log(data) -> dict | str:
	"""Omit BOL PDF/PNG binaries from transaction logs."""
	if not isinstance(data, dict):
		return data
	safe = deepcopy(data)
	images = safe.get("images")
	if isinstance(images, dict):
		for key, value in list(images.items()):
			images[key] = _omit_binary(value)
	elif isinstance(images, list):
		safe["images"] = [_omit_binary(value) for value in images]
	elif isinstance(images, str):
		safe["images"] = _omit_binary(images)
	return safe


def _omit_binary(value):
	text = str(value or "")
	if len(text) > 200:
		return f"<omitted {len(text)} chars>"
	return value


def _party(
	quote_data: dict,
	*,
	account: str,
	name,
	address,
	city,
	state,
	postal,
	country,
	contact_name,
	contact_phone,
) -> dict:
	return {
		"account": str(account or DEFAULT_SANDBOX_ACCOUNT).strip() or DEFAULT_SANDBOX_ACCOUNT,
		"name": str(name or "").strip() or "Shipper Co",
		"address1": str(address or "12 S. Main").strip() or "12 S. Main",
		"city": str(city or "").strip() or "Unknown",
		"stateProvince": str(state or "").strip() or "XX",
		"postalCode": str(postal or "").strip(),
		"country": _country(country or quote_data.get("origin_country")),
		"contact": {
			"name": str(contact_name or name or "Shipping Desk").strip() or "Shipping Desk",
			"phone": _phone(contact_phone),
		},
	}


def _handling_units(quote_data: dict) -> list[dict]:
	items = [item for item in (quote_data.get("items") or []) if isinstance(item, dict)]
	units = [_handling_unit(item, quote_data) for item in items]
	units = [unit for unit in units if unit]
	if units:
		return units
	return [
		_handling_unit(
			{
				"description": quote_data.get("commodity_description") or "Freight",
				"weight": quote_data.get("total_weight") or 1,
				"quantity": quote_data.get("pieces") or 1,
				"freight_class": quote_data.get("freight_class") or "70",
				"hazmat": quote_data.get("is_hazardous"),
				"packaging_units": "SKD",
				"units": "BOX",
			},
			quote_data,
		)
	]


def _handling_unit(item: dict, quote_data: dict) -> dict:
	weight = max(flt(item.get("weight") or quote_data.get("total_weight") or 1), 1)
	pieces = max(cint(item.get("qty") or item.get("quantity") or item.get("pieces") or 1), 1)
	count = max(cint(item.get("packaging_unit_count") or pieces), 1)
	classification = str(
		item.get("freight_class") or item.get("classification") or quote_data.get("freight_class") or "70"
	).strip() or "70"
	description = str(
		item.get("description") or item.get("item_name") or quote_data.get("commodity_description") or "Freight"
	).strip() or "Freight"
	return {
		"count": count,
		"type": _handling_type(item.get("packaging_units") or item.get("units") or "SKD"),
		"weight": _as_number(weight),
		"weightUnit": "Pounds",
		"lineItems": [
			{
				"description": description,
				"weight": _as_number(weight),
				"weightUnit": "Pounds",
				"pieces": pieces,
				"packagingType": _line_packaging(item.get("units") or item.get("packaging_type") or "BOX"),
				"classification": classification,
				"hazardous": bool(item.get("hazmat") or item.get("hazardous")),
			}
		],
	}


def _handling_type(value) -> str:
	raw = str(value or "").strip().upper()
	return HANDLING_UNIT_TYPES.get(raw, "SKD")


def _line_packaging(value) -> str:
	raw = str(value or "").strip().upper()
	return LINE_PACKAGING_TYPES.get(raw, "BOX")


def _pickup_datetime(quote_data: dict) -> str:
	raw = quote_data.get("pickup_date") or quote_data.get("requested_pickup_date")
	parsed = None
	if raw:
		text = str(raw).strip()
		for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
			try:
				parsed = datetime.strptime(text[:26], fmt)
				break
			except ValueError:
				continue
	if parsed is None:
		parsed = datetime.utcnow() + timedelta(days=1)
	if parsed.date() < datetime.utcnow().date():
		parsed = datetime.utcnow() + timedelta(days=1)
	return parsed.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(parsed.microsecond / 1000):03d}"


def _phone(value) -> str:
	digits = "".join(ch for ch in str(value or "") if ch.isdigit())
	if len(digits) >= 10:
		return digits[-10:]
	return "5552226666"


def _country(value) -> str:
	country = str(value or "USA").strip().upper()
	if country in {"US", "UNITED STATES", "UNITED STATES OF AMERICA"}:
		return "USA"
	return country or "USA"


def _as_number(value):
	number = flt(value or 0)
	if number == int(number):
		return int(number)
	return round(number, 2)


def quote_data_from_shipment(shipment, quote_request=None) -> dict:
	"""Rebuild a booking-style payload from a booked shipment for BOL PUT."""
	quote_request = quote_request or getattr(shipment, "quote_request", None)
	if isinstance(quote_request, str) and quote_request:
		import frappe

		if frappe.db.exists("LTL Quote Request", quote_request):
			quote_request = frappe.get_doc("LTL Quote Request", quote_request)
	qr = quote_request if quote_request and not isinstance(quote_request, str) else None
	items = []
	if qr:
		for row in getattr(qr, "line_items", None) or []:
			items.append(
				{
					"description": getattr(row, "description", None) or getattr(row, "item_name", None) or "",
					"item_name": getattr(row, "item_name", None) or "",
					"quantity": getattr(row, "quantity", None) or 1,
					"weight": getattr(row, "weight", None),
					"freight_class": getattr(row, "freight_class", None),
					"packaging_units": getattr(row, "packaging_units", None) or "",
					"packaging_unit_count": getattr(row, "packaging_unit_count", None),
					"units": getattr(row, "units", None) or "BOX",
					"hazmat": getattr(row, "hazmat", None),
				}
			)
	return {
		"quote_request": qr.name if qr else getattr(shipment, "quote_request", None),
		"origin_zip": getattr(qr, "origin_zip", None) or getattr(shipment, "bol_shipper_postal_code", None),
		"origin_city": getattr(qr, "origin_city", None) or getattr(shipment, "bol_shipper_city", None),
		"origin_state": getattr(qr, "origin_state", None) or getattr(shipment, "bol_shipper_state", None),
		"destination_zip": getattr(qr, "destination_zip", None) or getattr(shipment, "bol_consignee_postal_code", None),
		"destination_city": getattr(qr, "destination_city", None) or getattr(shipment, "bol_consignee_city", None),
		"destination_state": getattr(qr, "destination_state", None) or getattr(shipment, "bol_consignee_state", None),
		"shipper_name": getattr(shipment, "bol_shipper_name", None)
		or (getattr(qr, "shipper_company_name", None) if qr else None),
		"shipper_address": getattr(shipment, "bol_shipper_address1", None)
		or (getattr(qr, "shipper_address", None) if qr else None),
		"consignee_name": getattr(shipment, "bol_consignee_name", None)
		or (getattr(qr, "consignee_company_name", None) if qr else None),
		"consignee_address": getattr(shipment, "bol_consignee_address1", None)
		or (getattr(qr, "consignee_address", None) if qr else None),
		"contact_name": getattr(shipment, "bol_shipper_contact_name", None)
		or (getattr(qr, "contact_name", None) if qr else None),
		"contact_phone": getattr(shipment, "bol_shipper_contact_phone", None)
		or (getattr(qr, "contact_phone", None) if qr else None),
		"origin_contact_name": getattr(shipment, "bol_shipper_contact_name", None),
		"origin_contact_phone": getattr(shipment, "bol_shipper_contact_phone", None),
		"destination_contact_name": getattr(shipment, "bol_consignee_contact_name", None),
		"destination_contact_phone": getattr(shipment, "bol_consignee_contact_phone", None),
		"bill_to_name": getattr(shipment, "bol_bill_to_name", None),
		"bill_to_address": getattr(shipment, "bol_bill_to_address1", None),
		"bill_to_city": getattr(shipment, "bol_bill_to_city", None),
		"bill_to_state": getattr(shipment, "bol_bill_to_state", None),
		"bill_to_zip": getattr(shipment, "bol_bill_to_postal_code", None),
		"total_weight": getattr(qr, "total_weight", None) if qr else getattr(shipment, "bol_grand_total_weight", None),
		"pieces": getattr(qr, "pieces", None) if qr else getattr(shipment, "bol_total_quantity", None),
		"freight_class": getattr(qr, "freight_class", None) if qr else None,
		"commodity_description": (items[0].get("description") if items else "") or "Freight",
		"items": items,
		"payment_terms": getattr(shipment, "bol_payment_terms", None) or "Prepaid",
		"pickup_date": getattr(shipment, "pickup_date", None),
		"pro_number": getattr(shipment, "pro_number", None),
		"quoted_scac": getattr(shipment, "bol_scac", None),
	}
