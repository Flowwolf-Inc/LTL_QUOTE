# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""Whitelisted SMC3 Document, Dispatch, and Terminals helpers."""

from __future__ import annotations

import base64

import frappe
from frappe.utils.file_manager import save_file

from ltl_quote.carrier_network.smc3_token import AUTH_USER_MESSAGE, SMC3AuthError

DOCUMENT_TYPES = ("BL", "POD", "DR")
FILE_TYPES = ("PDF", "PNG")


def get_smc3_token() -> str:
	"""Return a live SMC3 Bearer token, throwing a clean message on auth failure."""
	from ltl_quote.carrier_network.adapters.smc3 import SMC3CarrierAdapter

	try:
		return SMC3CarrierAdapter().token_service.get_token()
	except SMC3AuthError:
		frappe.throw(AUTH_USER_MESSAGE)
	except Exception as exc:
		frappe.throw(str(exc) or AUTH_USER_MESSAGE)


def _adapter(carrier=None):
	from ltl_quote.carrier_network.adapters.smc3 import SMC3CarrierAdapter

	get_smc3_token()
	if carrier and not hasattr(carrier, "name"):
		carrier = frappe.get_doc("LTL Carrier", carrier)
	return SMC3CarrierAdapter(carrier)


def _get_smc3_shipment(shipment=None, shipment_name=None, pro_number=None):
	from ltl_quote.carrier_network.carrier_identity import CONNECTOR_SMC3, shipment_connector

	name = str(shipment or shipment_name or "").strip()
	if not name and str(pro_number or "").strip():
		name = frappe.db.get_value("LTL Shipment", {"pro_number": str(pro_number).strip()}, "name") or ""
	if not name or not frappe.db.exists("LTL Shipment", name):
		frappe.throw("A valid shipment ID is required.")
	doc = frappe.get_doc("LTL Shipment", name)
	frappe.has_permission("LTL Shipment", "read", doc=doc, throw=True)
	if shipment_connector(doc) != CONNECTOR_SMC3:
		frappe.throw("This action is only available for SMC3 shipments.")
	return doc


@frappe.whitelist()
def get_smc3_status(shipment=None, pro_number=None, carrier=None, scac=None, persist=0):
	"""GET SMC3 Status v1 and return FlowWolf tracking event dictionaries."""
	from frappe.utils import cint

	from ltl_quote.carrier_network.carrier_identity import CONNECTOR_SMC3, shipment_connector
	from ltl_quote.carrier_network.smc3_bol import quote_data_from_shipment
	from ltl_quote.carrier_network.smc3_dispatch import parse_status_events

	persist = cint(persist)
	name = str(shipment or "").strip()
	pro = str(pro_number or "").strip()
	doc = None
	if name and frappe.db.exists("LTL Shipment", name):
		doc = frappe.get_doc("LTL Shipment", name)
		frappe.has_permission("LTL Shipment", "read", doc=doc, throw=True)
		if shipment_connector(doc) != CONNECTOR_SMC3:
			frappe.throw("This action is only available for SMC3 shipments.")
	elif pro:
		found = frappe.db.get_value("LTL Shipment", {"pro_number": pro}, "name")
		if found:
			candidate = frappe.get_doc("LTL Shipment", found)
			frappe.has_permission("LTL Shipment", "read", doc=candidate, throw=True)
			if shipment_connector(candidate) == CONNECTOR_SMC3:
				doc = candidate

	pro = pro or str(getattr(doc, "pro_number", None) or "").strip()
	if not pro:
		frappe.throw("A PRO number is required to query SMC3 status.")

	if persist and doc:
		from ltl_quote.visibility.tracker import ShipmentTracker

		frappe.has_permission("LTL Shipment", "write", doc=doc, throw=True)
		tracker_result = ShipmentTracker(doc).refresh()
		doc.reload()
		events = format_flowwolf_status_events(
			[
				{
					"event_datetime": row.event_datetime,
					"status_code": row.status_code,
					"status_description": row.status_description,
					"location": row.location,
					"is_exception": row.is_exception,
					"exception_type": row.exception_type,
					"source": row.source,
				}
				for row in (doc.get("tracking_events") or [])
			],
			source=str(getattr(doc, "carrier", None) or "SMC3"),
		)
		return {
			"status": "success",
			"ok": True,
			"shipment": doc.name,
			"pro_number": pro,
			"scac": str(getattr(doc, "bol_scac", None) or "").strip().upper(),
			"events": events,
			"has_exception": bool(tracker_result.get("has_exception") or doc.has_exception),
			"current_status": doc.current_status,
			"current_location": doc.current_location,
			"message": (
				"SMC3 status retrieved." if events else "No tracking events returned for this PRO yet."
			),
		}

	adapter = _adapter(carrier or (doc.carrier if doc else None))
	quote_data = quote_data_from_shipment(doc) if doc else {"pro_number": pro}
	quote_data["pro_number"] = pro
	passed_scac = str(scac or "").strip().upper()
	if passed_scac and passed_scac not in {"SMC3", "SMC"}:
		quote_data["quoted_scac"] = passed_scac
	data = adapter.get_status(pro, quote_data=quote_data)
	if not isinstance(data, dict) or not data:
		frappe.throw(
			"SMC3 status request returned no data. A network SCAC is required when tracking without a booked shipment."
		)
	msg = data.get("messageStatus") if isinstance(data.get("messageStatus"), dict) else {}
	if str(msg.get("status") or "").upper() not in {"", "PASS"}:
		frappe.throw(msg.get("message") or "SMC3 status request failed.")
	source = "SMC3"
	if doc and getattr(doc, "carrier", None):
		source = str(
			frappe.db.get_value("LTL Carrier", doc.carrier, "carrier_name") or doc.carrier or "SMC3"
		)
	events = format_flowwolf_status_events(parse_status_events(data), source=source)
	return {
		"status": "success",
		"ok": True,
		"shipment": doc.name if doc else None,
		"pro_number": pro,
		"scac": str(data.get("scac") or quote_data.get("quoted_scac") or "").strip().upper(),
		"events": events,
		"raw": data,
		"message": "SMC3 status retrieved." if events else "No tracking events returned for this PRO yet.",
	}


def format_flowwolf_status_events(events, source: str = "SMC3") -> list[dict]:
	"""Normalize SMC3 / tracker events into the FlowWolf tracking dictionary."""
	out = []
	for ev in events or []:
		if not isinstance(ev, dict):
			continue
		out.append(
			{
				"event_datetime": ev.get("event_datetime"),
				"status_code": ev.get("status_code") or "",
				"status_description": ev.get("status_description") or "",
				"location": ev.get("location") or "",
				"is_exception": int(bool(ev.get("is_exception"))),
				"exception_type": ev.get("exception_type") or None,
				"source": ev.get("source") or source,
			}
		)
	return out


@frappe.whitelist()
def get_smc3_document(shipment=None, document_type="BL", file_type="PDF", scac=None, pro_number=None):
	"""Fetch an SMC3 Document API file (BL, POD, or DR) as PDF/PNG and attach it."""
	from ltl_quote.carrier_network.smc3_bol import quote_data_from_shipment

	document_type = str(document_type or "BL").strip().upper() or "BL"
	file_type = str(file_type or "PDF").strip().upper() or "PDF"
	if document_type not in DOCUMENT_TYPES:
		frappe.throw("document_type must be BL, POD, or DR.")
	if file_type not in FILE_TYPES:
		frappe.throw("file_type must be PDF or PNG.")

	doc = _get_smc3_shipment(shipment, pro_number=pro_number)
	frappe.has_permission("LTL Shipment", "write", doc=doc, throw=True)
	quote_data = quote_data_from_shipment(doc)
	passed_pro = str(pro_number or "").strip()
	if passed_pro:
		quote_data["pro_number"] = passed_pro
	passed_scac = str(scac or "").strip().upper()
	if passed_scac and passed_scac not in {"SMC3", "SMC"}:
		quote_data["quoted_scac"] = passed_scac
	adapter = _adapter(doc.carrier)
	live_scac = str(
		quote_data.get("quoted_scac") or quote_data.get("scac") or getattr(doc, "bol_scac", None) or ""
	).strip().upper()
	if live_scac in {"SMC3", "SMC"}:
		live_scac = ""
	live_pro = str(quote_data.get("pro_number") or getattr(doc, "pro_number", None) or "").strip()
	live_bol = str(quote_data.get("bol_number") or getattr(doc, "bol_number", None) or "").strip()
	if not adapter._is_sandbox_mode():
		if not live_scac or live_scac == "SMCA":
			frappe.throw("A network SCAC is required to fetch this SMC3 document.")
		if not live_pro and not live_bol:
			frappe.throw("A PRO or BOL number is required to fetch this SMC3 document.")
	result = adapter.get_document(
		doc,
		quote_data=quote_data,
		document_type=document_type,
		file_type=file_type,
		raise_on_empty=True,
	)
	attached = _attach_smc3_document(doc, result, document_type=document_type, file_type=file_type)
	return {
		"status": "success",
		"ok": True,
		"shipment": doc.name,
		"document_type": document_type,
		"file_type": file_type,
		"pro_number": result.get("pro_number") or doc.pro_number,
		"bol_number": result.get("bol_number") or doc.bol_number,
		"scac": result.get("scac") or "",
		"file_url": attached.get("file_url") or "",
		"document_binary": result.get("document_binary") or "",
		"pod_name": attached.get("pod_name") or "",
		"message": attached.get("message") or f"SMC3 {document_type} {file_type} retrieved.",
	}


@frappe.whitelist()
def get_pickup(shipment=None, shipment_name=None, number=None):
	"""Live GET of an SMC3 pickup confirmation."""
	from ltl_quote.carrier_network.pickup import apply_pickup_response_to_shipment, shipment_pickup_summary

	doc = _get_smc3_shipment(shipment, shipment_name)
	frappe.has_permission("LTL Shipment", "write", doc=doc, throw=True)
	pickup_number = str(number or doc.pickup_number or "").strip()
	if not pickup_number:
		frappe.throw("This shipment does not have an SMC3 pickup confirmation yet.")
	adapter = _adapter(doc.carrier)
	result = adapter.get_pickup(pickup_number, shipment=doc)
	if result.get("ok"):
		apply_pickup_response_to_shipment(doc, result, save=True)
		doc.reload()
	return {
		"status": "success",
		"ok": True,
		"shipment": doc.name,
		"pickup": shipment_pickup_summary(doc),
		"pickup_number": result.get("pickup_number") or pickup_number,
		"pickup_status": result.get("pickup_status") or doc.pickup_status,
		"raw": result.get("raw") or {},
	}


@frappe.whitelist()
def update_pickup_request(shipment=None, shipment_name=None, number=None):
	"""PUT an SMC3 pickup update (dispatchCode UPDATE)."""
	from ltl_quote.carrier_network.pickup import shipment_pickup_summary

	doc = _get_smc3_shipment(shipment, shipment_name)
	frappe.has_permission("LTL Shipment", "write", doc=doc, throw=True)
	pickup_number = str(number or doc.pickup_number or "").strip()
	if not pickup_number:
		frappe.throw("This shipment does not have an SMC3 pickup confirmation to update.")
	adapter = _adapter(doc.carrier)
	result = adapter.update_pickup_request(pickup_number, shipment=doc)
	doc.reload()
	return {
		"status": "success",
		"ok": True,
		"shipment": doc.name,
		"pickup": shipment_pickup_summary(doc),
		"pickup_number": result.get("pickup_number") or pickup_number,
		"pickup_status": result.get("pickup_status") or doc.pickup_status,
		"message": "SMC3 pickup updated.",
	}


@frappe.whitelist()
def get_carrier_terminal_info(scac=None, postal_code=None, shipment=None, lane=None):
	"""GET SMC3 terminal locations for a SCAC and postal code."""
	from ltl_quote.carrier_network.smc3_bol import quote_data_from_shipment

	scac = str(scac or "").strip().upper()
	if scac in {"SMC3", "SMC"}:
		scac = ""
	postal_code = str(postal_code or "").strip()
	lane = str(lane or "").strip().lower()
	doc = None
	if shipment and frappe.db.exists("LTL Shipment", str(shipment).strip()):
		doc = frappe.get_doc("LTL Shipment", str(shipment).strip())
		frappe.has_permission("LTL Shipment", "read", doc=doc, throw=True)

	quote_data = quote_data_from_shipment(doc) if doc else {}
	if not scac:
		scac = str(
			(doc and getattr(doc, "bol_scac", None)) or quote_data.get("quoted_scac") or quote_data.get("scac") or ""
		).strip().upper()
		if scac in {"SMC3", "SMC"}:
			scac = ""
	if not postal_code and doc:
		if lane in {"destination", "dest", "consignee", "to"}:
			postal_code = str(
				quote_data.get("destination_zip") or getattr(doc, "bol_consignee_postal_code", None) or ""
			).strip()
		else:
			postal_code = str(
				quote_data.get("origin_zip") or getattr(doc, "bol_shipper_postal_code", None) or ""
			).strip()

	adapter = _adapter(doc.carrier if doc else None)
	if not scac and adapter._is_sandbox_mode():
		scac = "SMCA"
	if not scac:
		frappe.throw("A network SCAC is required to look up SMC3 terminal information.")
	result = adapter.get_carrier_terminal_info(scac, postal_code)
	normalized = _normalize_terminals(result.get("terminals"), result.get("scac") or scac)
	return {
		"status": "success",
		"ok": True,
		"scac": result.get("scac") or scac,
		"postal_code": str(result.get("postal_code") or postal_code).strip(),
		"lane": lane or "origin",
		"terminals": normalized,
		"raw": result.get("raw") or {},
	}


def _normalize_terminals(raw, default_scac: str = "") -> list[dict]:
	items = raw
	if isinstance(raw, dict):
		items = raw.get("terminals") or raw.get("terminal") or raw.get("locations") or raw.get("terminalList")
		if items is None:
			if any(key in raw for key in ("name", "terminalName", "address", "city", "postalCode")):
				items = [raw]
			else:
				items = []
	if not isinstance(items, list):
		items = [items] if items else []
	out = []
	for item in items:
		if not isinstance(item, dict):
			continue
		if "messageStatus" in item and len(item) <= 2:
			continue
		normalized = _normalize_terminal(item, default_scac)
		if normalized:
			out.append(normalized)
	return out


def _normalize_terminal(item: dict, default_scac: str = "") -> dict:
	addr = item.get("address") if isinstance(item.get("address"), dict) else {}
	loc = item.get("location") if isinstance(item.get("location"), dict) else {}
	contact = item.get("contact") if isinstance(item.get("contact"), dict) else {}
	hours = item.get("hours") or item.get("operatingHours") or item.get("hoursOfOperation") or item.get("operating_hours")
	return {
		"name": _first_text(
			item.get("name"),
			item.get("terminalName"),
			item.get("terminal_name"),
			item.get("locationName"),
			"Carrier Terminal",
		),
		"scac": _first_text(item.get("scac"), default_scac),
		"address": _first_text(
			item.get("address1"),
			item.get("addressLine1"),
			item.get("street"),
			addr.get("address1"),
			addr.get("line1"),
			addr.get("street"),
			loc.get("address1"),
			item.get("address") if not isinstance(item.get("address"), dict) else "",
		),
		"city": _first_text(item.get("city"), addr.get("city"), loc.get("city")),
		"state": _first_text(
			item.get("state"),
			item.get("stateProvince"),
			addr.get("state"),
			addr.get("stateProvince"),
			loc.get("state"),
		),
		"zip": _first_text(
			item.get("postalCode"),
			item.get("zip"),
			addr.get("postalCode"),
			addr.get("zip"),
			loc.get("postalCode"),
		),
		"phone": _first_text(
			item.get("phone"),
			item.get("phoneNumber"),
			item.get("telephone"),
			contact.get("phone"),
			addr.get("phone"),
		),
		"contact": _first_text(
			item.get("contactName"),
			item.get("contact") if not isinstance(item.get("contact"), dict) else "",
			contact.get("name"),
			item.get("manager"),
		),
		"hours": _format_terminal_hours(hours),
	}


def _format_terminal_hours(hours) -> str:
	if not hours:
		return ""
	if isinstance(hours, str):
		return hours.strip()
	if isinstance(hours, list):
		parts = []
		for row in hours:
			if isinstance(row, str) and row.strip():
				parts.append(row.strip())
			elif isinstance(row, dict):
				day = _first_text(row.get("day"), row.get("dayOfWeek"), row.get("name"))
				open_t = _first_text(row.get("open"), row.get("openTime"), row.get("start"))
				close_t = _first_text(row.get("close"), row.get("closeTime"), row.get("end"))
				window = " – ".join([p for p in (open_t, close_t) if p])
				if day and window:
					parts.append(f"{day}: {window}")
				elif window:
					parts.append(window)
		return "; ".join(parts)
	if isinstance(hours, dict):
		open_t = _first_text(hours.get("open"), hours.get("openTime"), hours.get("start"))
		close_t = _first_text(hours.get("close"), hours.get("closeTime"), hours.get("end"))
		if open_t or close_t:
			return " – ".join([p for p in (open_t, close_t) if p])
		parts = []
		for day, val in hours.items():
			if str(day).lower() in {"open", "close", "opentime", "closetime", "start", "end"}:
				continue
			if isinstance(val, str) and val.strip():
				parts.append(f"{day}: {val.strip()}")
			elif isinstance(val, dict):
				window = " – ".join(
					[
						p
						for p in (
							_first_text(val.get("open"), val.get("openTime")),
							_first_text(val.get("close"), val.get("closeTime")),
						)
						if p
					]
				)
				if window:
					parts.append(f"{day}: {window}")
		return "; ".join(parts)
	return str(hours).strip()


def _first_text(*values) -> str:
	for value in values:
		if value is None or isinstance(value, (dict, list)):
			continue
		text = str(value).strip()
		if text:
			return text
	return ""


def _attach_smc3_document(shipment, result: dict, document_type: str, file_type: str) -> dict:
	document_type = str(document_type or "BL").upper()
	file_type = str(file_type or "PDF").upper()
	pro_number = str(result.get("pro_number") or getattr(shipment, "pro_number", None) or "").strip()
	bol_number = str(result.get("bol_number") or getattr(shipment, "bol_number", None) or "").strip()
	shipment_name = shipment.name

	if file_type == "PDF":
		file_bytes = _decode_pdf_bytes(result.get("document_binary") or "")
		if not file_bytes:
			frappe.throw(f"SMC3 {document_type} binary was not a usable PDF.")
		ext = "pdf"
	else:
		from ltl_quote.carrier_network.adapters.smc3 import _decode_png_bytes

		images = result.get("images") or []
		raw = images[0] if images else result.get("document_binary")
		file_bytes = _decode_png_bytes(raw)
		if not file_bytes:
			frappe.throw(f"SMC3 {document_type} binary was not a usable PNG.")
		ext = "png"

	filename = f"SMC3_{document_type}_{pro_number or bol_number or shipment_name}.{ext}"
	file_doc = save_file(
		fname=filename,
		content=file_bytes,
		dt="LTL Shipment",
		dn=shipment_name,
		is_private=0,
		decode=False,
	)
	file_url = file_doc.file_url
	absolute_url = f"{frappe.utils.get_url()}{file_url}"
	payload = {"file_url": absolute_url if absolute_url.startswith("http") else file_url}

	if document_type == "BL":
		frappe.db.set_value(
			"LTL Shipment",
			shipment_name,
			{
				"bol_document": file_url,
				"bol_document_url": absolute_url,
				"bol_document_type": "Bill of Lading",
			},
			update_modified=False,
		)
		payload["message"] = "SMC3 bill of lading attached."
	elif document_type == "POD":
		pod_name = _upsert_ltl_pod(shipment, file_url, document_type=document_type)
		payload["pod_name"] = pod_name
		payload["message"] = "SMC3 POD attached."
	elif document_type == "DR":
		payload["message"] = "SMC3 delivery receipt attached."
	frappe.db.commit()
	return payload


def _upsert_ltl_pod(shipment, file_url: str, document_type: str = "POD") -> str:
	existing = frappe.db.get_value("LTL POD", {"shipment": shipment.name, "source": "Carrier"}, "name")
	values = {
		"status": "POD Received",
		"source": "Carrier",
		"shipment": shipment.name,
		"carrier": shipment.carrier,
		"bol_number": shipment.bol_number,
		"pro_number": shipment.pro_number,
		"pod_document": file_url,
		"notes": f"Retrieved from SMC3 Document API ({document_type}).",
	}
	if existing:
		doc = frappe.get_doc("LTL POD", existing)
		doc.update(values)
		doc.save(ignore_permissions=True)
		return doc.name
	doc = frappe.get_doc({"doctype": "LTL POD", "naming_series": "LTL-POD-.YYYY.-.#####", **values})
	doc.insert(ignore_permissions=True)
	return doc.name


def _decode_pdf_bytes(document_binary: str) -> bytes | None:
	raw = str(document_binary or "").strip()
	if not raw:
		return None
	if "," in raw and raw.lower().startswith("data:"):
		raw = raw.split(",", 1)[1]
	raw = "".join(raw.split())
	try:
		file_bytes = base64.b64decode(raw)
	except Exception:
		return None
	marker = file_bytes.find(b"%PDF")
	if marker < 0 or len(file_bytes[marker:]) < 100:
		return None
	return file_bytes[marker:]
