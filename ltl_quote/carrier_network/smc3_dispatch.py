# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""SMC3 Dispatch + Status payload builders and response helpers."""

from __future__ import annotations

from datetime import datetime

import frappe
from frappe.utils import flt, get_datetime

from ltl_quote.api.payload import freight_class_lookup_key, line_item_freight_class
from ltl_quote.carrier_network.pickup import resolve_pickup_window
from ltl_quote.carrier_network.smc3_bol import (
	canonical_bol_number,
	quote_data_from_shipment,
	require_email,
	require_phone,
	require_text,
)
from ltl_quote.carrier_network.tracking import (
	activity_label,
	is_exception_code,
	milestone_index_for_code,
	normalize_activity_code,
	text_implies_delivered,
)

DEFAULT_DISPATCH_BASE = "https://dispatch.smc3.com/dispatch/v3/app"
DEFAULT_STATUS_BASE = "https://status.smc3.com/status/v1/app"

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
		name=quote_data.get("shipper_name"),
		address=quote_data.get("shipper_address"),
		city=quote_data.get("origin_city"),
		state=quote_data.get("origin_state"),
		postal=quote_data.get("origin_zip"),
		country=quote_data.get("origin_country"),
		contact_name=quote_data.get("origin_contact_name") or quote_data.get("contact_name"),
		contact_phone=quote_data.get("origin_contact_phone") or quote_data.get("contact_phone"),
		contact_email=quote_data.get("origin_contact_email") or quote_data.get("contact_email"),
		party_label="Shipper",
	)
	destination = _dispatch_party(
		quote_data,
		name=quote_data.get("consignee_name"),
		address=quote_data.get("consignee_address"),
		city=quote_data.get("destination_city"),
		state=quote_data.get("destination_state"),
		postal=quote_data.get("destination_zip"),
		country=quote_data.get("destination_country"),
		contact_name=quote_data.get("destination_contact_name"),
		contact_phone=quote_data.get("destination_contact_phone"),
		contact_email=quote_data.get("destination_contact_email") or quote_data.get("consignee_email"),
		party_label="Consignee",
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
	if code in {"CANCEL", "UPDATE"}:
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
	party_label: str,
) -> dict:
	postal_code = require_text(postal, f"{party_label} Postal Code")
	return {
		"name": require_text(name, f"{party_label} Company Name"),
		"address": require_text(address, f"{party_label} Address"),
		"city": require_text(city, f"{party_label} City"),
		"stateProvince": require_text(state, f"{party_label} State"),
		"postalCode": postal_code,
		"country": _dispatch_country(country, postal_code),
		"contact": {
			"name": require_text(contact_name, f"{party_label} Contact Name"),
			"phone": require_phone(contact_phone, f"{party_label} Contact Phone"),
			"email": require_email(contact_email, f"{party_label} Contact Email"),
		},
	}


def _dispatch_requestor(quote_data: dict, origin: dict) -> dict:
	contact = origin.get("contact") if isinstance(origin.get("contact"), dict) else {}
	return {
		"name": require_text(
			quote_data.get("requestor_name") or quote_data.get("shipper_name") or origin.get("name"),
			"Requestor Company Name",
		),
		"contact": {
			"name": require_text(
				quote_data.get("requestor_contact_name") or contact.get("name"),
				"Requestor Contact Name",
			),
			"phone": require_phone(
				quote_data.get("requestor_phone") or contact.get("phone"),
				"Requestor Contact Phone",
			),
			"email": require_email(
				quote_data.get("requestor_email") or contact.get("email"),
				"Requestor Contact Email",
			),
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
	avail_ready, avail_close = _availability_datetimes(data)
	return {
		"ok": True,
		"pickup_number": str(pickup or "").strip(),
		"pickup_status": str(status or "Scheduled").strip() or "Scheduled",
		"pro_number": str(pro or "").strip(),
		"ready": ready or avail_ready,
		"close": close or avail_close,
		"transaction_id": str(data.get("transactionId") or "").strip(),
		"raw": data,
		"status": "acknowledged",
	}


def _availability_datetimes(data: dict) -> tuple:
	avail = data.get("pickupAvailability") if isinstance(data.get("pickupAvailability"), dict) else {}
	date = str(avail.get("date") or "").strip()
	if len(date) == 8 and date.isdigit():
		date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
	ready_raw = _normalize_dispatch_time(
		avail.get("readyTime") or avail.get("openTime") or avail.get("startTime")
	)
	close_raw = _normalize_dispatch_time(avail.get("closeTime"))
	if not date:
		return None, None
	try:
		ready = get_datetime(f"{date} {ready_raw}") if ready_raw else get_datetime(date)
		close = get_datetime(f"{date} {close_raw}") if close_raw else None
		return ready, close
	except Exception:
		return None, None


def _normalize_dispatch_time(value) -> str:
	raw = str(value or "").strip()
	if not raw:
		return ""
	digits = "".join(ch for ch in raw if ch.isdigit())
	if len(digits) >= 4:
		return f"{digits[:2]}:{digits[2:4]}:{digits[4:6] or '00'}"
	return raw


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
	actual_time = _parse_smc3_time(delivery.get("actualTime"))
	signature = str(
		delivery.get("signature")
		or delivery.get("signedBy")
		or delivery.get("podSignature")
		or delivery.get("receiverName")
		or ""
	).strip()
	delivered_at = _parse_event_datetime(_combine_smc3_date_time(delivery.get("actualDate"), delivery.get("actualTime")))
	for event in events:
		if pickup_date:
			event["pickup_date"] = pickup_date
		if estimated:
			event["estimated_delivery"] = estimated
		if actual:
			event["actual_delivery"] = actual
		if actual_time:
			event["actual_delivery_time"] = actual_time
		if signature:
			event["delivery_signature"] = signature
		if delivered_at and text_implies_delivered(event.get("status_code"), event.get("status_description")):
			event["event_datetime"] = event.get("event_datetime") or delivered_at

	if actual and not any(text_implies_delivered(ev.get("status_code"), ev.get("status_description")) for ev in events):
		events.append(
			{
				"event_datetime": delivered_at,
				"status_code": "D1",
				"status_description": "Delivered",
				"location": "",
				"is_exception": 0,
				"pickup_date": pickup_date,
				"estimated_delivery": estimated,
				"actual_delivery": actual,
				"actual_delivery_time": actual_time,
				"delivery_signature": signature,
			}
		)
	events.sort(key=lambda ev: ev.get("event_datetime") or datetime.min)
	return events


def _status_history_rows(data: dict) -> list:
	"""Merge statusHistory with the current status object without duplicating the latest scan."""
	rows = []
	seen = set()

	def add(row):
		if isinstance(row, str):
			text = row.strip()
			if not text:
				return
			code = text.upper()
			if any(
				str(existing.get("code") or existing.get("statusCode") or existing.get("status") or "").strip().upper()
				== code
				for existing in rows
			):
				return
			key = ("code", code)
			if key in seen:
				return
			seen.add(key)
			rows.append({"code": text, "description": text})
			return
		if not isinstance(row, dict):
			return
		key = (
			str(row.get("code") or row.get("statusCode") or row.get("status") or "").strip().upper(),
			str(row.get("utc") or "").strip()
			or _combine_smc3_date_time(row.get("date"), row.get("time")),
		)
		if key in seen:
			return
		seen.add(key)
		rows.append(row)

	history = data.get("statusHistory")
	if isinstance(history, list):
		for item in history:
			add(item)
	elif isinstance(history, dict):
		add(history)

	for extra in (
		data.get("shipmentStatus"),
		data.get("statuses"),
		data.get("events"),
		data.get("trackingEvents"),
		data.get("currentStatus"),
		data.get("status"),
	):
		if extra is None or extra is history:
			continue
		if isinstance(extra, list):
			for item in extra:
				add(item)
		else:
			add(extra)

	return rows


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
	if text_implies_delivered(code, description) and milestone_index_for_code(code) is None:
		code = "D1"
		description = description or "Delivered"
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
		"exception_type": _exception_type_for_row(row, description, exception),
	}


def _exception_type_for_row(row: dict, description: str, exception: bool) -> str | None:
	if not exception:
		return None
	explicit = str(row.get("exceptionType") or row.get("exception_type") or "").strip()
	if explicit:
		return explicit
	text = f"{description} {row.get('code') or ''}".lower()
	if "weather" in text:
		return "Weather"
	if "damage" in text or "damaged" in text:
		return "Damage"
	if "missed pickup" in text:
		return "Missed Pickup"
	if "delay" in text:
		return "Delay"
	return "Other"


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


def _parse_smc3_time(value) -> str | None:
	"""Normalize SMC3 HHMM / HH:MM[:SS] into a Frappe Time value."""
	text = str(value or "").strip()
	if not text:
		return None
	digits = "".join(ch for ch in text if ch.isdigit())
	if len(digits) < 3:
		return None
	digits = digits.ljust(6, "0")[:6]
	hours, minutes, seconds = int(digits[:2]), int(digits[2:4]), int(digits[4:6])
	if hours > 23 or minutes > 59 or seconds > 59:
		return None
	return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


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

	PRO lookup: proNumber only  (GET /status/v1/app/{SCAC}?proNumber=…)
	BOL lookup: bol + pickupDate + origin/destination postal + country
	Mixing keys returns Invalid Query Params.
	"""
	pro_params = status_pro_query_params(pro_number, quote_data, shipment)
	if pro_params:
		return pro_params
	return status_bol_query_params(quote_data, shipment)


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


def sandbox_status_query_params(config: dict | None = None, pro_number: str = "") -> dict:
	"""Optional Status v1 lookup from carrier notes. Never invents sample PRO/BOL/ZIP values."""
	cfg = config or {}
	pro = str(cfg.get("status_demo_pro") or cfg.get("status_pro") or pro_number or "").strip()
	if pro:
		return {"proNumber": pro}
	return {}


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
