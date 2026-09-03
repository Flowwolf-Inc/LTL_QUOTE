# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""Inbound carrier webhooks (SMC3 Status Push)."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import now_datetime

from ltl_quote.carrier_network.smc3_bol import canonical_bol_number
from ltl_quote.carrier_network.tracking import (
	_STATUS_ORDER,
	activity_label,
	normalize_activity_code,
	shipment_status_from_activity,
)

QUOTE_STATUS_OPTIONS = {
	"Draft",
	"Aggregating",
	"Quoted",
	"Accepted",
	"Booked",
	"Cancelled",
	"Error",
}
LEGACY_STATUS_MAP = {
	"PICKED_UP": "In Transit",
	"IN_TRANSIT": "In Transit",
	"OUT_FOR_DELIVERY": "Out for Delivery",
	"DELIVERED": "Delivered",
	"D1": "Delivered",
	"VOIDED": "Cancelled",
	"CANCELLED": "Cancelled",
	"EXCEPTION": "Exception",
}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def smc3_status_update(**kwargs):
	"""Receive an SMC3 Status webhook and advance the matching LTL shipment.

	Expected JSON (SMC3 Status Push)::

	    {
	        "scac": "CNWY",
	        "referenceNumbers": {"bol": "...", "pro": "..."},
	        "status": "Out for Delivery"
	    }

	`status` may also be a Status v1 object. Returns HTTP 200 on success and
	HTTP 404 when no LTL Shipment / LTL Quote Request matches the BOL or PRO.
	"""
	try:
		payload = _request_payload(kwargs)
		parsed = _parse_status_payload(payload)
		if not parsed["bol"] and not parsed["pro"]:
			return _http(400, {"status": "error", "message": "BOL or PRO number is required."})

		shipment_name, quote_name = _find_quote_documents(parsed["bol"], parsed["pro"])
		if not shipment_name:
			return _http(404, {"status": "error", "message": "Shipment not found."})

		result = _apply_status_update(
			shipment_name=shipment_name,
			quote_name=quote_name,
			parsed=parsed,
			payload=payload,
		)
		return _http(200, result)
	except frappe.DoesNotExistError:
		return _http(404, {"status": "error", "message": "Shipment not found."})
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="SMC3 Status Webhook", message=frappe.get_traceback())
		return _http(500, {"status": "error", "message": "Unable to process SMC3 status update."})


def _http(status_code: int, body: dict) -> dict:
	frappe.local.response["http_status_code"] = int(status_code)
	return body


def _request_payload(kwargs=None) -> dict:
	payload = {}
	form = getattr(frappe.local, "form_dict", None)
	if isinstance(form, dict):
		payload.update(form)
	if isinstance(kwargs, dict):
		payload.update(kwargs)
	payload.pop("cmd", None)
	payload.pop("csrf_token", None)

	nested = payload.get("data")
	if isinstance(nested, dict) and not payload.get("referenceNumbers"):
		payload = {**nested, **payload}

	return {key: value for key, value in payload.items() if key not in {"cmd", "csrf_token"}}


def _parse_status_payload(payload: dict) -> dict:
	raw = payload if isinstance(payload, dict) else {}
	refs = raw.get("referenceNumbers") or raw.get("reference_numbers") or {}
	if isinstance(refs, list):
		mapped = {}
		for item in refs:
			if not isinstance(item, dict):
				continue
			kind = str(item.get("type") or item.get("kind") or "").strip().lower()
			number = str(item.get("number") or item.get("value") or "").strip()
			if kind and number:
				mapped[kind] = number
		refs = mapped
	elif not isinstance(refs, dict):
		refs = {}

	bol = (
		refs.get("bol")
		or refs.get("BOL")
		or refs.get("billOfLading")
		or refs.get("bolNumber")
		or raw.get("bol")
		or raw.get("bolNumber")
		or raw.get("bol_number")
		or ""
	)
	pro = (
		refs.get("pro")
		or refs.get("proNumber")
		or refs.get("pronumber")
		or refs.get("PRO")
		or raw.get("pro")
		or raw.get("proNumber")
		or raw.get("pro_number")
		or ""
	)
	scac = raw.get("scac") or raw.get("SCAC") or raw.get("carrierScac") or raw.get("carrier") or ""
	status = raw.get("status") if "status" in raw else raw.get("currentStatus") or raw.get("shipmentStatus")
	return {
		"scac": str(scac or "").strip().upper(),
		"bol": str(bol or "").strip(),
		"pro": str(pro or "").strip(),
		"status": status,
	}


def _find_quote_documents(bol: str, pro: str) -> tuple[str | None, str | None]:
	shipment_name = _find_by_references("LTL Shipment", bol, pro)
	quote_name = _find_by_references("LTL Quote Request", bol, pro)

	if shipment_name and not quote_name:
		quote_name = frappe.db.get_value("LTL Shipment", shipment_name, "quote_request")

	if quote_name and not shipment_name:
		shipment_name = frappe.db.get_value("LTL Shipment", {"quote_request": quote_name}, "name")

	return (
		str(shipment_name).strip() if shipment_name else None,
		str(quote_name).strip() if quote_name else None,
	)


def _find_by_references(doctype: str, bol: str, pro: str) -> str | None:
	if pro and bol:
		name = _first_match(doctype, {"pro_number": pro, "bol_number": bol})
		if name:
			return name
		name = _first_match(doctype, {"pro_number": pro, "bol_number": ["like", f"{bol}%"]})
		if name:
			return name
	if pro:
		name = _first_match(doctype, {"pro_number": pro})
		if name:
			return name
	if bol:
		name = _first_match(doctype, {"bol_number": bol})
		if name:
			return name
		for row in frappe.get_all(
			doctype,
			filters={"bol_number": ["like", f"{bol}%"]},
			fields=["name", "bol_number"],
			limit=20,
			order_by="modified desc",
			ignore_permissions=True,
		):
			stored = str(row.bol_number or "").strip()
			if stored == bol or canonical_bol_number(stored) == bol:
				return row.name
	return None


def _first_match(doctype: str, filters: dict) -> str | None:
	rows = frappe.get_all(
		doctype,
		filters=filters,
		pluck="name",
		limit=1,
		order_by="modified desc",
		ignore_permissions=True,
	)
	return rows[0] if rows else None


def _apply_status_update(*, shipment_name: str | None, quote_name: str | None, parsed: dict, payload: dict) -> dict:
	from ltl_quote.carrier_network.smc3_dispatch import parse_status_events

	events = parse_status_events(payload) or _events_from_status(parsed.get("status"))
	if events:
		events = sorted(events, key=lambda e: str(e.get("event_datetime") or ""))
	latest = events[-1] if events else None
	if not isinstance(latest, dict):
		latest = {
			"event_datetime": now_datetime(),
			"status_code": "INFO",
			"status_description": "Cargo Movement Updated",
			"location": "",
			"is_exception": 0,
		}

	status_code = normalize_activity_code(latest.get("status_code"))
	description = str(latest.get("status_description") or activity_label(status_code) or "").strip()
	mapped_status = _map_shipment_status(status_code, description)
	if not mapped_status:
		from ltl_quote.carrier_network.tracking import highest_shipment_status

		mapped_status = highest_shipment_status(events)
	comment = _timeline_comment(parsed.get("scac"), description or mapped_status or status_code)

	updated_shipment = None
	if shipment_name and frappe.db.exists("LTL Shipment", shipment_name):
		updated_shipment = _update_shipment(
			shipment_name, latest, mapped_status, comment, parsed.get("scac"), events=events
		)

	updated_quote = None
	if quote_name and frappe.db.exists("LTL Quote Request", quote_name):
		updated_quote = _update_quote_request(quote_name, mapped_status, comment)

	if not updated_shipment:
		frappe.throw(_("Shipment not found."), frappe.DoesNotExistError)

	frappe.db.commit()
	return {
		"status": "ok",
		"shipment": updated_shipment,
		"quote": updated_quote,
		"carrier_status": description or mapped_status or status_code,
		"mapped_status": mapped_status,
	}


def _events_from_status(status) -> list[dict]:
	from ltl_quote.carrier_network.smc3_dispatch import parse_status_events

	return parse_status_events({"status": status}) if status else []


def _map_shipment_status(code: str | None, description: str | None) -> str | None:
	mapped = shipment_status_from_activity(code, description)
	if mapped:
		return mapped
	mapped = LEGACY_STATUS_MAP.get(normalize_activity_code(code))
	if mapped:
		return mapped
	return LEGACY_STATUS_MAP.get(normalize_activity_code(description))


def _status_rank(value: str | None) -> int:
	try:
		return _STATUS_ORDER.index(str(value or "").strip())
	except ValueError:
		return -1


def _should_advance(current: str | None, mapped: str | None) -> bool:
	if not mapped:
		return False
	if mapped in {"Cancelled", "Exception"}:
		return True
	return _status_rank(mapped) >= _status_rank(current)


def _timeline_comment(scac: str | None, description: str) -> str:
	carrier = str(scac or "SMC3").strip() or "SMC3"
	label = str(description or "Cargo Movement Updated").strip()
	return f"SMC3 status update ({frappe.utils.escape_html(carrier)}): {frappe.utils.escape_html(label)}"


def _update_shipment(name: str, event: dict, mapped_status: str | None, comment: str, scac: str | None, events=None) -> str:
	from ltl_quote.carrier_network.tracking import delivery_details_from_events

	doc = frappe.get_doc("LTL Shipment", name)
	status_code = normalize_activity_code(event.get("status_code"))
	description = str(event.get("status_description") or "").strip()
	existing = [
		(normalize_activity_code(row.status_code), str(row.status_description or "").strip())
		for row in (doc.tracking_events or [])
	]
	if (status_code, description) not in existing:
		doc.append(
			"tracking_events",
			{
				"event_datetime": event.get("event_datetime") or now_datetime(),
				"status_code": status_code or "INFO",
				"status_description": description or activity_label(status_code),
				"location": event.get("location") or "",
				"is_exception": int(event.get("is_exception") or 0),
				"exception_type": event.get("exception_type"),
				"source": f"{scac or 'SMC3'} Webhook",
			},
		)

	if event.get("is_exception"):
		doc.has_exception = 1
	doc.current_status = description or mapped_status or doc.current_status
	if event.get("location"):
		doc.current_location = event.get("location")
	doc.last_tracking_update = now_datetime()

	if _should_advance(doc.status, mapped_status):
		doc.status = mapped_status

	details = delivery_details_from_events(list(events or []) + [event])
	actual = details.get("actual_delivery_date") or (mapped_status == "Delivered" and event.get("event_datetime"))
	if actual:
		try:
			from frappe.utils import getdate

			doc.actual_delivery_date = getdate(actual)
		except Exception:
			pass
		if mapped_status != "Delivered":
			doc.status = "Delivered"
			mapped_status = "Delivered"
	if details.get("actual_delivery_time") and doc.meta.has_field("actual_delivery_time"):
		doc.actual_delivery_time = details["actual_delivery_time"]
	if details.get("delivery_signature") and doc.meta.has_field("delivery_signature"):
		doc.delivery_signature = details["delivery_signature"]

	doc.save(ignore_permissions=True)
	doc.add_comment("Comment", comment)
	return doc.name


def _update_quote_request(name: str, mapped_status: str | None, comment: str) -> str:
	doc = frappe.get_doc("LTL Quote Request", name)
	# Quote Request select does not include In Transit / Delivered.
	if mapped_status == "Cancelled" and mapped_status in QUOTE_STATUS_OPTIONS and doc.status != "Cancelled":
		doc.status = "Cancelled"
		doc.save(ignore_permissions=True)
	doc.add_comment("Comment", comment)
	return doc.name
