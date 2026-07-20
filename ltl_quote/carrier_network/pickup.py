"""Dayton Pickup API payload builders and shipment persistence helpers."""

from __future__ import annotations

from datetime import datetime, timedelta

import frappe
from frappe.utils import cint, get_datetime, getdate, now_datetime, today

from ltl_quote.carrier_network.accessorials import (
	build_accessorial_items,
	dayton_pickup_accessorials,
	normalize_service_group,
)
from ltl_quote.utils.booking import resolve_shipper_context
from ltl_quote.utils.location import resolve_us_location

PICKUP_TERMINAL_STATUSES = {"Cancelled", "PickedUp"}
MAX_PICKUP_COMMENT_CHARS = 230


def _load_quote_request(shipment):
	if not shipment.quote_request:
		return None
	if frappe.db.exists("LTL Quote Request", shipment.quote_request):
		return frappe.get_doc("LTL Quote Request", shipment.quote_request)
	return None


def filter_pickup_accessorial_items(accessorials: list) -> list:
	"""Keep accessorial rows that may apply to PUT /api/Pickup (origin + shipment-level)."""
	filtered = []
	for item in accessorials or []:
		group = normalize_service_group(getattr(item, "service_group", None))
		if group in {"pickup", "load", "delivery", ""}:
			filtered.append(item)
	return filtered


def default_pickup_window(pickup_date=None) -> tuple[datetime, datetime]:
	"""Return a default ready/close window on a weekday (09:00–17:00 local)."""
	base_date = getdate(pickup_date or today())
	ready = get_datetime(f"{base_date} 09:00:00")
	close = get_datetime(f"{base_date} 17:00:00")
	now = now_datetime()
	if ready < now:
		next_day = getdate(add_business_days(now, 1))
		ready = get_datetime(f"{next_day} 09:00:00")
		close = get_datetime(f"{next_day} 17:00:00")
	return ready, close


def add_business_days(dt: datetime, days: int):
	value = getdate(dt)
	added = 0
	while added < days:
		value = value + timedelta(days=1)
		if value.weekday() < 5:
			added += 1
	return value


def format_pickup_datetime(value) -> str:
	"""Dayton expects shipper-local time without timezone: YYYY-MM-DDThh:mm:ss."""
	if not value:
		return ""
	dt = get_datetime(value)
	return dt.strftime("%Y-%m-%dT%H:%M:%S")


def resolve_pickup_window(shipment) -> tuple[datetime, datetime]:
	ready = shipment.get("pickup_ready") if isinstance(shipment, dict) else getattr(shipment, "pickup_ready", None)
	close = shipment.get("pickup_close") if isinstance(shipment, dict) else getattr(shipment, "pickup_close", None)
	pickup_date = shipment.get("pickup_date") if isinstance(shipment, dict) else getattr(shipment, "pickup_date", None)
	if ready and close:
		return get_datetime(ready), get_datetime(close)
	return default_pickup_window(pickup_date)


def validate_pickup_window(ready: datetime, close: datetime) -> None:
	now = now_datetime()
	if ready.weekday() >= 5 or close.weekday() >= 5:
		frappe.throw("Dayton pickups cannot be scheduled on weekends.")
	if ready.date() > (now.date() + timedelta(days=45)):
		frappe.throw("Pickup ready time must be within 45 days.")
	if ready <= now:
		frappe.throw("Pickup ready time must be in the future.")
	if close <= ready:
		frappe.throw("Pickup close time must be after ready time.")
	if (close - ready) < timedelta(hours=2):
		frappe.throw("Pickup ready time must be at least 2 hours before close time.")
	if ready.hour > 17 or (ready.hour == 17 and ready.minute > 0):
		frappe.throw("Pickup ready time cannot be after 5:00 PM local time.")


def _build_reference_numbers(shipment, quote_request=None) -> list[dict]:
	"""Build Dayton pickup detail referenceNumbers (BillOfLadingNumber / ShipperNumber / PO)."""
	refs = []
	bol = str(getattr(shipment, "bol_number", None) or "").strip()
	pro = str(getattr(shipment, "pro_number", None) or "").strip()
	if bol:
		refs.append(
			{
				"referenceNumberType": "BillOfLadingNumber",
				"referenceNumber": bol,
			}
		)
	if pro:
		refs.append(
			{
				"referenceNumberType": "ShipperNumber",
				"referenceNumber": pro,
			}
		)
	if quote_request:
		po = str(getattr(quote_request, "po_number", None) or getattr(quote_request, "customer_po", None) or "").strip()
		if po:
			refs.append(
				{
					"referenceNumberType": "PurchaseOrder",
					"referenceNumber": po,
				}
			)
	return refs


def _pickup_notification_emails() -> list[str]:
	emails: list[str] = []
	if frappe.session.user:
		email = frappe.db.get_value("User", frappe.session.user, "email")
		if email and "@" in str(email):
			emails.append(str(email).strip())
	if emails:
		return emails
	try:
		platform = frappe.get_single("LTL Platform Settings")
		fallback = str(getattr(platform, "default_contact_email", None) or "").strip()
		if fallback and "@" in fallback:
			return [fallback]
	except Exception:
		pass
	return []


def _pickup_comments(shipment, quote_request=None) -> str:
	parts = []
	custom = str(getattr(shipment, "pickup_comments", None) or "").strip()
	if custom:
		parts.append(custom)
	if not parts:
		parts.append("Scheduled via LTL Quote platform")
	combined = " | ".join(parts)
	return combined[:MAX_PICKUP_COMMENT_CHARS]


def build_pickup_payload_from_shipment(shipment, adapter) -> dict:
	"""Build Dayton PUT /api/Pickup JSON from an LTL Shipment."""
	quote_request = _load_quote_request(shipment)
	shipper = resolve_shipper_context({}, quote_request)

	origin_zip = ""
	origin_city = ""
	origin_state = ""
	destination_zip = ""
	handling_units = 1
	weight = 1
	is_hazardous = False

	if quote_request:
		origin_zip = str(quote_request.origin_zip or "")
		destination_zip = str(quote_request.destination_zip or "")
		origin_city = str(quote_request.origin_city or "")
		origin_state = str(quote_request.origin_state or "")
		handling_units = cint(quote_request.pieces) or 1
		weight = cint(quote_request.total_weight) or 1
		is_hazardous = bool(getattr(quote_request, "is_hazardous", 0))

	origin_city, origin_state = resolve_us_location(origin_zip, origin_city, origin_state)
	if not str(destination_zip or "").strip():
		frappe.throw("Destination ZIP is required for Dayton pickup scheduling.")
	if not origin_state:
		frappe.throw(
			"Origin state is required for Dayton pickup scheduling. Provide origin state or a valid US origin ZIP."
		)
	if not str(origin_zip or "").strip():
		frappe.throw("Origin ZIP is required for Dayton pickup scheduling.")

	handling_units = max(1, min(999, handling_units))
	weight = max(1, min(40000, weight))

	ready_dt, close_dt = resolve_pickup_window(shipment)
	validate_pickup_window(ready_dt, close_dt)

	accessorial_items = filter_pickup_accessorial_items(
		build_accessorial_items(getattr(quote_request, "accessorials", None) if quote_request else None)
	)
	pickup_accessorials = dayton_pickup_accessorials(accessorial_items, adapter.carrier_doc)

	contact_name = (
		getattr(shipment, "bol_shipper_contact_name", None)
		or shipper.get("contact_name")
		or "Shipping Desk"
	)
	contact_phone = adapter._dayton_contact_phone(
		getattr(shipment, "bol_shipper_contact_phone", None),
		shipper.get("contact_phone"),
	)

	customer_ref = str(getattr(shipment, "pickup_comments", None) or "").strip()[:25] or None
	is_test = customer_ref == "TESTING"

	comments = _pickup_comments(shipment, quote_request)
	detail = {
		"destinationZip": destination_zip,
		"handlingUnits": handling_units,
		"weight": weight,
		"isHazardous": is_hazardous,
	}
	reference_numbers = _build_reference_numbers(shipment, quote_request)
	if reference_numbers:
		detail["referenceNumbers"] = reference_numbers

	confirmation_emails = _pickup_notification_emails()

	return {
		"customerReferenceNumber": customer_ref,
		"sendConfirmationTo": confirmation_emails,
		"sendReceiptTo": confirmation_emails,
		"details": [detail],
		"shipper": {
			"name": str(
				getattr(shipment, "bol_shipper_name", None)
				or shipper.get("shipper_name")
				or "Main Warehouse Dispatch"
			),
			"address": {
				"address1": str(
					getattr(shipment, "bol_shipper_address1", None)
					or shipper.get("shipper_address")
					or "123 Logistics Way"
				),
				"city": str(origin_city or "Dayton"),
				"state": str(origin_state or "OH"),
				"zip": origin_zip,
			},
		},
		"ready": format_pickup_datetime(ready_dt),
		"close": format_pickup_datetime(close_dt),
		"contact": {
			"name": str(contact_name),
			"phone": str(contact_phone),
			"extension": None,
			"fax": None,
			"email": None,
		},
		"requester": {
			"name": str(frappe.session.user or "LTL Quote"),
			"phone": str(contact_phone),
			"extension": None,
			"fax": None,
			"email": None,
		},
		"accessorials": pickup_accessorials,
		"comments": comments,
		"pickupInstructions": None,
		"isTest": is_test,
	}


def normalize_pickup_response(data: dict) -> dict:
	"""Normalize Dayton pickup GET/PUT/POST responses."""
	if not data:
		return {"ok": False, "pickup": {}, "raw": {}}
	pickup_number = data.get("pickupNumber")
	status = data.get("status")
	shipments = data.get("shipments") or data.get("items") or []
	first_shipment = shipments[0] if shipments else {}
	return {
		"ok": True,
		"pickup_number": pickup_number,
		"pickup_status": status,
		"pickup_psid": first_shipment.get("psid") or first_shipment.get("id") or data.get("psid"),
		"pro_number": first_shipment.get("pro"),
		"ready": data.get("ready"),
		"close": data.get("close"),
		"is_editable": data.get("isEditable"),
		"items": shipments,
		"trace_id": data.get("traceId"),
		"raw": data,
	}


def apply_pickup_response_to_shipment(shipment, pickup_data: dict, *, save: bool = True) -> None:
	"""Persist pickup fields on LTL Shipment from a normalized pickup response."""
	raw = pickup_data.get("raw") or pickup_data
	pickup_number = pickup_data.get("pickup_number") or raw.get("pickupNumber")
	if pickup_number:
		shipment.pickup_number = str(pickup_number)
		shipment.carrier_confirmation = str(pickup_number)
	if pickup_data.get("pickup_psid"):
		shipment.pickup_psid = cint(pickup_data.get("pickup_psid"))
	elif raw.get("psid"):
		shipment.pickup_psid = cint(raw.get("psid"))
	status = pickup_data.get("pickup_status") or raw.get("status")
	if status:
		shipment.pickup_status = str(status)
	if pickup_data.get("ready") or raw.get("ready"):
		shipment.pickup_ready = get_datetime(pickup_data.get("ready") or raw.get("ready"))
	if pickup_data.get("close") or raw.get("close"):
		shipment.pickup_close = get_datetime(pickup_data.get("close") or raw.get("close"))
	pro = pickup_data.get("pro_number")
	if pro and not shipment.pro_number:
		shipment.pro_number = str(pro)
	if save:
		shipment.dispatch_status = map_pickup_status_to_dispatch_status(status, pickup_data.get("status"))
		if shipment.dispatch_status == "Acknowledged" and shipment.status == "Booked":
			shipment.status = "Dispatched"
		shipment.save(ignore_permissions=True)
		frappe.db.commit()


def map_pickup_status_to_dispatch_status(pickup_status: str | None, adapter_status: str | None = None) -> str:
	if adapter_status == "acknowledged":
		return "Acknowledged"
	value = str(pickup_status or "").strip()
	if value in {"Assigned", "PartnerScheduling", "PartnerScheduled", "SeeDetails"}:
		return "Acknowledged"
	if value == "Cancelled":
		return "Failed"
	if value == "PickedUp":
		return "Acknowledged"
	return "Sent to Carrier"


def shipment_pickup_summary(shipment, *, live: bool = False, adapter=None) -> dict:
	"""Return pickup block for APIs/UI from stored shipment fields."""
	summary = {
		"pickup_number": getattr(shipment, "pickup_number", None) or "",
		"psid": cint(getattr(shipment, "pickup_psid", None) or 0) or None,
		"status": getattr(shipment, "pickup_status", None) or "",
		"ready": getattr(shipment, "pickup_ready", None),
		"close": getattr(shipment, "pickup_close", None),
		"comments": getattr(shipment, "pickup_comments", None) or "",
		"is_editable": None,
		"items": [],
	}
	if not summary["pickup_number"]:
		return summary
	if live and adapter:
		result = adapter.get_pickup(summary["pickup_number"])
		if result.get("ok"):
			apply_pickup_response_to_shipment(shipment, result, save=True)
			summary.update(
				{
					"status": result.get("pickup_status") or summary["status"],
					"ready": result.get("ready") or summary["ready"],
					"close": result.get("close") or summary["close"],
					"is_editable": result.get("is_editable"),
					"items": result.get("items") or [],
					"psid": result.get("pickup_psid") or summary["psid"],
				}
			)
	return summary


def resolve_pickup_cancel_number(shipment) -> str:
	return (
		str(getattr(shipment, "pickup_number", None) or "").strip()
		or str(getattr(shipment, "pickup_psid", None) or "").strip()
		or str(getattr(shipment, "carrier_confirmation", None) or "").strip()
		or str(getattr(shipment, "bol_number", None) or "").strip()
	)
