# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""SMC3 Dispatch + Status payload builders and response helpers."""

from __future__ import annotations

from datetime import datetime

from frappe.utils import flt, get_datetime

from ltl_quote.api.payload import freight_class_lookup_key, line_item_freight_class
from ltl_quote.carrier_network.pickup import resolve_pickup_window
from ltl_quote.carrier_network.smc3_bol import (
	DEFAULT_DOCUMENT_DEMO_BOL,
	_phone,
	canonical_bol_number,
	quote_data_from_shipment,
)
from ltl_quote.carrier_network.tracking import activity_label, is_exception_code, normalize_activity_code

DEFAULT_DISPATCH_BASE = "https://dispatch.smc3.com/dispatch/v3/app"
DEFAULT_STATUS_BASE = "https://status.smc3.com/status/v1/app"
DEFAULT_STATUS_DEMO_BOL = DEFAULT_DOCUMENT_DEMO_BOL
DEFAULT_STATUS_DEMO_PICKUP_DATE = "20260907"
DEFAULT_STATUS_DEMO_ORIGIN = "30269"
DEFAULT_STATUS_DEMO_DEST = "40213"

SCHEDULED_PICKUP_STATUSES = {
	"Scheduled",
	"Assigned",
	"PickedUp",
	"Success",
	"PartnerScheduled",
	"PartnerScheduling",
	"SeeDetails",
	"Acknowledged",
}


def pickup_already_scheduled(shipment) -> bool:
	status = str(getattr(shipment, "pickup_status", None) or "").strip()
	if status == "Cancelled":
		return False
	return status in SCHEDULED_PICKUP_STATUSES


def build_dispatch_payload(
	shipment,
	quote_data: dict | None = None,
	*,
	dispatch_code: str = "CREATE",
	account: str = "",
	pickup_number: str = "",
) -> dict:
	"""Map a booked shipment onto the SMC3 Dispatch v3 body."""
	quote_data = quote_data or quote_data_from_shipment(shipment)
	origin = _dispatch_party(
		quote_data,
		name=quote_data.get("shipper_name") or "Demo Shipper Company",
		address=quote_data.get("shipper_address") or "123 Demo Shipper way",
		city=quote_data.get("origin_city"),
		state=quote_data.get("origin_state"),
		postal=quote_data.get("origin_zip"),
		country=quote_data.get("origin_country"),
		contact_name=quote_data.get("origin_contact_name") or quote_data.get("contact_name") or "John Doe",
		contact_phone=quote_data.get("origin_contact_phone") or quote_data.get("contact_phone"),
		contact_email=quote_data.get("origin_contact_email") or quote_data.get("contact_email"),
		email_fallback="shipperContactPerson@email.com",
	)
	destination = _dispatch_party(
		quote_data,
		name=quote_data.get("consignee_name") or "Demo Consignee Company",
		address=quote_data.get("consignee_address") or "456 Demo Consignee way",
		city=quote_data.get("destination_city"),
		state=quote_data.get("destination_state"),
		postal=quote_data.get("destination_zip"),
		country=quote_data.get("destination_country"),
		contact_name=quote_data.get("destination_contact_name") or "Jane Doe",
		contact_phone=quote_data.get("destination_contact_phone"),
		contact_email=quote_data.get("destination_contact_email") or quote_data.get("consignee_email"),
		email_fallback="consigneeContactPerson@email.com",
	)
	ready_dt, close_dt = resolve_pickup_window(shipment)
	code = str(dispatch_code or "CREATE").strip().upper() or "CREATE"
	payload = {
		"dispatchCode": code,
		"service": {"level": str(quote_data.get("service_level") or "STND")},
		"payment": {
			"terms": str(quote_data.get("payment_terms") or "Prepaid"),
			"payer": "Shipper",
		},
		"pickupAvailability": _pickup_availability(ready_dt, close_dt),
		"commodities": _commodities(quote_data),
		"origin": origin,
		"destination": destination,
		"requestor": _dispatch_requestor(quote_data, origin),
	}
	if code == "CANCEL":
		pickup = str(
			pickup_number or getattr(shipment, "pickup_number", None) or ""
		).strip()
		if pickup:
			payload["referenceNumbers"] = [
				{"assignedBy": "Customer", "type": "pickup", "number": pickup}
			]
	return payload


def _pickup_availability(ready_dt, close_dt) -> dict:
	ready = get_datetime(ready_dt)
	close = get_datetime(close_dt)
	return {
		"date": ready.strftime("%Y%m%d"),
		"startTime": ready.strftime("%H%M%S"),
		"endTime": close.strftime("%H%M%S"),
		"closeTime": close.strftime("%H%M%S"),
	}


def _commodities(quote_data: dict) -> list[dict]:
	rows = []
	for item in quote_data.get("items") or []:
		if not isinstance(item, dict):
			continue
		weight = flt(item.get("weight") or quote_data.get("total_weight") or 0)
		if weight <= 0:
			continue
		rows.append(_commodity_row(item, quote_data, weight))
	if rows:
		return rows
	return [_commodity_row({}, quote_data, flt(quote_data.get("total_weight") or 1))]


def _commodity_row(item: dict, quote_data: dict, weight) -> dict:
	key = freight_class_lookup_key(
		line_item_freight_class(item, quote_data.get("freight_class") or "70") or "70"
	) or "70"
	return {
		"packagingType": _packaging_type(item.get("packaging_type") or item.get("units") or item.get("packaging_units")),
		"pieces": _as_string_number(
			item.get("qty") or item.get("quantity") or item.get("pieces") or quote_data.get("pieces") or 1
		),
		"classification": str(int(key)) if str(key).isdigit() else str(key),
		"weight": _as_string_number(weight or 1),
		"description": str(
			item.get("description") or item.get("item_name") or quote_data.get("commodity_description") or "Freight"
		).strip()
		or "Freight",
	}


def _dispatch_party(
	quote_data: dict,
	*,
	name,
	address,
	city,
	state,
	postal,
	country,
	contact_name,
	contact_phone,
	contact_email=None,
	email_fallback: str = "",
) -> dict:
	postal_code = str(postal or "").strip()
	return {
		"name": str(name or "").strip() or "Shipper Co",
		"address": str(address or "12 S. Main").strip() or "12 S. Main",
		"city": str(city or "").strip() or "Unknown",
		"stateProvince": str(state or "").strip() or "XX",
		"postalCode": postal_code,
		"country": _dispatch_country(country, postal_code),
		"contact": {
			"name": str(contact_name or name or "Shipping Desk").strip() or "Shipping Desk",
			"phone": _phone(contact_phone) if contact_phone else "8002723425",
			"email": str(contact_email or email_fallback or "shipperContactPerson@email.com").strip()
			or email_fallback
			or "shipperContactPerson@email.com",
		},
	}


def _dispatch_requestor(quote_data: dict, origin: dict) -> dict:
	contact = origin.get("contact") if isinstance(origin.get("contact"), dict) else {}
	return {
		"name": str(
			quote_data.get("requestor_name") or quote_data.get("shipper_name") or origin.get("name") or "Demo Requestor Company"
		).strip()
		or "Demo Requestor Company",
		"contact": {
			"name": str(
				quote_data.get("requestor_contact_name") or contact.get("name") or "Joe Murphy"
			).strip()
			or "Joe Murphy",
			"phone": str(quote_data.get("requestor_phone") or contact.get("phone") or "8002723425").strip()
			or "8002723425",
			"email": str(
				quote_data.get("requestor_email") or contact.get("email") or "requestorContactPerson@email.com"
			).strip()
			or "requestorContactPerson@email.com",
		},
	}


def _dispatch_country(value, postal="") -> str:
	country = str(value or "").strip().upper()
	if country in {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}:
		return "USA"
	if country in {"CA", "CAN", "CANADA"}:
		return "CAN"
	compact = str(postal or "").replace(" ", "").upper()
	if compact and compact[0].isalpha():
		return "CAN"
	return country or "USA"


def parse_dispatch_response(data: dict, *, ready=None, close=None) -> dict:
	"""Normalize an SMC3 Dispatch v3 response onto the shared pickup shape."""
	data = data if isinstance(data, dict) else {}
	refs = _dispatch_reference_map(data.get("referenceNumbers"))
	pickup = (
		refs.get("pickup")
		or data.get("pickupConfirmation")
		or data.get("pickupNumber")
		or data.get("confirmationNumber")
		or data.get("dispatchConfirmation")
		or ""
	)
	pro = refs.get("pro") or ""
	status = data.get("pickupStatus") or data.get("dispatchStatus") or "Scheduled"
	if isinstance(status, dict):
		status = status.get("code") or status.get("description") or "Scheduled"
	message_status = data.get("messageStatus") if isinstance(data.get("messageStatus"), dict) else {}
	if str(message_status.get("status") or "").upper() == "PASS" and not pickup:
		pickup = str(data.get("transactionId") or "").strip()
	return {
		"ok": True,
		"pickup_number": str(pickup or "").strip(),
		"pickup_status": str(status or "Scheduled").strip() or "Scheduled",
		"pro_number": str(pro or "").strip(),
		"ready": ready,
		"close": close,
		"transaction_id": str(data.get("transactionId") or "").strip(),
		"raw": data,
		"status": "acknowledged",
	}


def _dispatch_reference_map(raw) -> dict:
	refs = {}
	if isinstance(raw, list):
		for item in raw:
			if not isinstance(item, dict):
				continue
			kind = str(item.get("type") or "").strip().lower()
			number = str(item.get("number") or "").strip()
			if kind and number and kind not in refs:
				refs[kind] = number
		return refs
	if isinstance(raw, dict):
		for key, value in raw.items():
			kind = str(key or "").strip().lower()
			number = str(value or "").strip()
			if kind and number:
				refs[kind] = number
	return refs


def parse_status_events(data: dict) -> list[dict]:
	"""Normalize SMC3 Status v1 payload (statusHistory + current status) into tracker events."""
	data = data if isinstance(data, dict) else {}
	raw_events = _status_history_rows(data)
	events = []
	for row in raw_events:
		parsed = _parse_status_row(row)
		if parsed:
			events.append(parsed)

	transit = data.get("transit") if isinstance(data.get("transit"), dict) else {}
	delivery = transit.get("delivery") if isinstance(transit.get("delivery"), dict) else {}
	pickup_date = _parse_smc3_date(transit.get("pickupDate"))
	estimated = _parse_smc3_date(delivery.get("estimatedDate"))
	actual = _parse_smc3_date(delivery.get("actualDate"))
	for event in events:
		if pickup_date:
			event["pickup_date"] = pickup_date
		if estimated:
			event["estimated_delivery"] = estimated
		if actual:
			event["actual_delivery"] = actual
	return events


def _status_history_rows(data: dict) -> list:
	"""SMC3 Status v1 returns statusHistory plus a current status object."""
	history = data.get("statusHistory")
	if isinstance(history, list) and history:
		return history
	if isinstance(history, dict):
		return [history]

	raw = (
		data.get("shipmentStatus")
		or data.get("statuses")
		or data.get("events")
		or data.get("trackingEvents")
		or []
	)
	if isinstance(raw, dict):
		raw = [raw]
	if raw:
		return list(raw)

	current = data.get("currentStatus") or data.get("status")
	if isinstance(current, dict):
		return [current]
	if isinstance(current, str) and current.strip():
		return [{"code": current, "description": current}]
	return []


def _parse_status_row(row) -> dict | None:
	if isinstance(row, str):
		code = normalize_activity_code(row)
		if not code:
			return None
		return {
			"event_datetime": None,
			"status_code": code,
			"status_description": activity_label(code),
			"location": "",
			"is_exception": 1 if is_exception_code(code) else 0,
		}
	if not isinstance(row, dict):
		return None
	code = normalize_activity_code(
		row.get("statusCode")
		or row.get("code")
		or row.get("activityCode")
		or row.get("type")
		or row.get("status")
	)
	description = str(
		row.get("carrierDescription")
		or row.get("statusDescription")
		or row.get("description")
		or row.get("message")
		or row.get("status")
		or activity_label(code)
		or ""
	).strip()
	if not code and description:
		code = normalize_activity_code(description)
	if not code and not description:
		return None
	when = _status_event_datetime(row)
	city = str(row.get("city") or row.get("cityName") or "").strip()
	state = str(row.get("state") or row.get("stateProvince") or "").strip()
	location = str(row.get("location") or "").strip()
	if not location:
		location = ", ".join(part for part in (city, state) if part)
	exception = bool(row.get("isException") or row.get("exception") or is_exception_code(code))
	if description and ("exception" in description.lower() or "delay" in description.lower()):
		exception = True
	return {
		"event_datetime": when,
		"status_code": code or "INFO",
		"status_description": description or activity_label(code),
		"location": location,
		"is_exception": 1 if exception else 0,
		"exception_type": str(row.get("exceptionType") or "").strip() if exception else None,
	}


def _status_event_datetime(row: dict):
	utc = str(row.get("utc") or "").strip()
	if utc:
		parsed = _parse_event_datetime(utc)
		if parsed:
			return parsed
	combined = _combine_smc3_date_time(row.get("date"), row.get("time"))
	if combined:
		parsed = _parse_event_datetime(combined)
		if parsed:
			return parsed
	when = (
		row.get("eventDateTime")
		or row.get("dateTime")
		or row.get("timestamp")
		or row.get("statusDateTime")
		or row.get("date")
	)
	return _parse_event_datetime(when)


def _combine_smc3_date_time(date_value, time_value) -> str:
	date = str(date_value or "").replace("-", "").replace("/", "").strip()
	time = str(time_value or "").replace(":", "").replace(".", "").strip()
	if len(date) >= 8 and time:
		return date[:8] + time.ljust(6, "0")[:6]
	return date[:8] if len(date) >= 8 else ""


def _parse_smc3_date(value):
	text = str(value or "").replace("-", "").replace("/", "").strip()
	if len(text) < 8 or not text[:8].isdigit():
		return None
	try:
		return datetime.strptime(text[:8], "%Y%m%d").date()
	except ValueError:
		return None


def _parse_event_datetime(value):
	if not value:
		return None
	text = str(value).strip()
	if not text:
		return None
	try:
		parsed = get_datetime(text.replace("Z", "+00:00") if text.endswith("Z") else text)
		if parsed is not None and getattr(parsed, "tzinfo", None):
			return parsed.replace(tzinfo=None)
		return parsed
	except Exception:
		pass
	for fmt, slice_len in (
		("%Y%m%d%H%M%S", 14),
		("%Y%m%d", 8),
		("%Y-%m-%dT%H:%M:%S", 19),
		("%Y-%m-%d", 10),
	):
		try:
			return datetime.strptime(text[:slice_len], fmt)
		except ValueError:
			continue
	return None


def _packaging_type(value) -> str:
	raw = str(value or "").strip().upper()
	if raw in {"PAT", "PLT", "PALLET", "PALLETS", "SKD", "SKID"}:
		return "PAT"
	if raw in {"CTN", "CARTON", "BOX", "BOXES"}:
		return "CTN"
	if raw in {"DRM", "DRUM", "DRUMS"}:
		return "DRM"
	if raw in {"PCS", "PIECE", "PIECES"}:
		return "PCS"
	return "PAT"


def _as_string_number(value) -> str:
	number = flt(value or 0)
	if number == int(number):
		return str(int(number))
	return f"{number:.2f}".rstrip("0").rstrip(".")


def status_query_params(pro_number: str, quote_data: dict | None = None, shipment=None) -> dict:
	"""GET /status query string. SMC3 accepts one lookup mode at a time.

	BOL lookup: bol + pickupDate + origin/destination postal + country
	PRO lookup: proNumber only
	Mixing keys returns Invalid Query Params.
	"""
	bol_params = status_bol_query_params(quote_data, shipment)
	if bol_params:
		return bol_params
	return status_pro_query_params(pro_number, quote_data, shipment)


def status_pro_query_params(pro_number: str, quote_data: dict | None = None, shipment=None) -> dict:
	quote_data = quote_data or {}
	pro = str(
		pro_number or quote_data.get("pro_number") or getattr(shipment, "pro_number", None) or ""
	).strip()
	if pro:
		return {"proNumber": pro}
	return {}


def status_bol_query_params(quote_data: dict | None = None, shipment=None) -> dict:
	"""Match GET /status?bol=&pickupDate=&originPostalCode=&originCountry=&destinationPostalCode=&destinationCountry=."""
	quote_data = quote_data or {}
	bol = canonical_bol_number(
		quote_data.get("bol_number") or getattr(shipment, "bol_number", None),
		shipment,
		quote_data,
	)
	origin = str(
		quote_data.get("origin_zip") or getattr(shipment, "bol_shipper_postal_code", None) or ""
	).strip()
	dest = str(
		quote_data.get("destination_zip") or getattr(shipment, "bol_consignee_postal_code", None) or ""
	).strip()
	pickup = _status_pickup_yyyymmdd(quote_data, shipment)
	if not (bol and origin and dest and pickup):
		return {}
	return {
		"bol": bol,
		"pickupDate": pickup,
		"originPostalCode": origin,
		"originCountry": _dispatch_country(quote_data.get("origin_country"), origin),
		"destinationPostalCode": dest,
		"destinationCountry": _dispatch_country(quote_data.get("destination_country"), dest),
	}


def sandbox_status_query_params(config: dict | None = None) -> dict:
	"""Canned Status v1 demo query (SMC3 sandbox sample)."""
	cfg = config or {}
	return {
		"bol": str(cfg.get("status_demo_bol") or DEFAULT_STATUS_DEMO_BOL).strip() or DEFAULT_STATUS_DEMO_BOL,
		"pickupDate": str(cfg.get("status_demo_pickup_date") or DEFAULT_STATUS_DEMO_PICKUP_DATE).strip()
		or DEFAULT_STATUS_DEMO_PICKUP_DATE,
		"originPostalCode": str(cfg.get("status_demo_origin") or DEFAULT_STATUS_DEMO_ORIGIN).strip()
		or DEFAULT_STATUS_DEMO_ORIGIN,
		"originCountry": "USA",
		"destinationPostalCode": str(cfg.get("status_demo_dest") or DEFAULT_STATUS_DEMO_DEST).strip()
		or DEFAULT_STATUS_DEMO_DEST,
		"destinationCountry": "USA",
	}


def _status_pickup_yyyymmdd(quote_data: dict | None = None, shipment=None) -> str:
	quote_data = quote_data or {}
	pickup_date = (
		getattr(shipment, "pickup_date", None)
		or quote_data.get("pickup_date")
		or getattr(shipment, "booked_on", None)
	)
	if not pickup_date:
		return ""
	try:
		return get_datetime(pickup_date).strftime("%Y%m%d")
	except Exception:
		text = str(pickup_date).replace("-", "").replace("/", "").strip()
		return text[:8] if len(text) >= 8 and text[:8].isdigit() else ""


def status_request_body(pro_number: str, quote_data: dict | None = None, shipment=None) -> dict:
	quote_data = quote_data or {}
	pro = str(pro_number or quote_data.get("pro_number") or getattr(shipment, "pro_number", None) or "").strip()
	bol = canonical_bol_number(
		quote_data.get("bol_number") or getattr(shipment, "bol_number", None),
		shipment,
		quote_data,
	)
	pickup = str(getattr(shipment, "pickup_number", None) or "").strip()
	origin = str(quote_data.get("origin_zip") or getattr(shipment, "bol_shipper_postal_code", None) or "").strip()
	dest = str(quote_data.get("destination_zip") or getattr(shipment, "bol_consignee_postal_code", None) or "").strip()
	pickup_date = _status_pickup_yyyymmdd(quote_data, shipment)
	refs = {}
	if pro:
		refs["pro"] = pro
	if bol:
		refs["bol"] = bol
	if pickup:
		refs["pickup"] = pickup
	body = {"referenceNumbers": refs} if refs else {}
	if origin:
		body["origin"] = {"postalCode": origin, "country": _dispatch_country(quote_data.get("origin_country"), origin)}
	if dest:
		body["destination"] = {
			"postalCode": dest,
			"country": _dispatch_country(quote_data.get("destination_country"), dest),
		}
	if pickup_date:
		body["pickupDate"] = pickup_date
	return body


def parse_dispatch_response_messages(payload) -> list[dict]:
	"""Normalize GET /responseMessages/dispatch into code/status/message rows."""
	if isinstance(payload, list):
		items = payload
	elif isinstance(payload, dict):
		items = payload.get("statuses") or payload.get("messages") or payload.get("data") or []
		if not isinstance(items, list):
			items = []
	else:
		items = []
	rows = []
	seen = set()
	for item in items:
		if not isinstance(item, dict):
			continue
		code = str(item.get("code") or "").strip()
		if not code or code in seen:
			continue
		seen.add(code)
		status = str(item.get("status") or "").strip().upper()
		if status not in {"PASS", "FAIL", "WARNING"}:
			status = "FAIL"
		rows.append(
			{
				"code": code,
				"status": status,
				"message": str(item.get("message") or "").strip(),
				"resolution": str(item.get("resolution") or "").strip(),
				"api_last_modified": str(item.get("lastModified") or "").strip(),
			}
		)
	return rows


def dispatch_message_for_code(code: str) -> dict | None:
	"""Return a stored Dispatch response-message row by SMC3 code."""
	import frappe

	name = str(code or "").strip()
	if not name:
		return None
	try:
		if not frappe.db.exists("DocType", "LTL SMC3 Dispatch Message"):
			return None
		if not frappe.db.exists("LTL SMC3 Dispatch Message", name):
			return None
	except Exception:
		return None
	return frappe.db.get_value(
		"LTL SMC3 Dispatch Message",
		name,
		["code", "status", "message", "resolution"],
		as_dict=True,
	)


def format_dispatch_status_message(status: dict | None) -> str:
	"""Build a user-facing Dispatch error using messageStatus plus the catalog."""
	status = status if isinstance(status, dict) else {}
	code = str(status.get("code") or "").strip()
	message = str(status.get("message") or "").strip()
	resolution = str(status.get("resolution") or "").strip()
	catalog = dispatch_message_for_code(code) if code else None
	if catalog:
		message = message or str(catalog.get("message") or "").strip()
		resolution = resolution or str(catalog.get("resolution") or "").strip()
	parts = []
	head = message or "SMC3 dispatch request failed."
	if code:
		head = f"[{code}] {head}"
	parts.append(head)
	if resolution and resolution not in head:
		parts.append(resolution)
	info = status.get("information")
	if isinstance(info, list):
		extra = " ".join(str(item).strip() for item in info if str(item or "").strip())
		if extra:
			parts.append(extra)
	elif isinstance(info, str) and info.strip():
		parts.append(info.strip())
	return " ".join(parts)
