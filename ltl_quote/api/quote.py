"""
FLOWWOLF Unified Multi-Carrier Rating API

Single gateway endpoint that accepts one standard payload, fans out to all
enabled carrier adapters in parallel, merges responses, ranks quotes, and
returns a normalized JSON schema (BlueShip / project44 style).
"""

import json

import frappe
from frappe.utils import cint, flt, now_datetime

from ltl_quote.api.carrier_mapping import load_carrier_for_rating, resolve_carrier_id
from ltl_quote.api.payload import apply_default_handling_dimensions, default_handling_dimensions, line_item_freight_class, parse_rating_payload
from ltl_quote.carrier_network.accessorials import build_accessorial_items_from_payload
from ltl_quote.carrier_network.adapters.base import ShipmentRequest
from ltl_quote.decision_engine.recommender import rank_quotes
from ltl_quote.carrier_network.registry import get_adapter
from ltl_quote.carrier_network.smc3_token import (
	TFORCE_AUTH_USER_MESSAGE,
	is_auth_error_text,
	is_tforce_connector_text,
)
from ltl_quote.rate_engine.aggregator import RateAggregator
from ltl_quote.utils.booking import resolve_shipper_context, resolve_shipment_bol_image_url, resolve_shipment_bol_url
from ltl_quote.utils.currency import get_quote_currency
from ltl_quote.utils.location import enrich_location_fields, resolve_us_location
from ltl_quote.utils.transaction_log import log_api_transaction


@frappe.whitelist(allow_guest=False)
def get_ltl_rates(payload=None, **kwargs):
	"""
	FLOWWOLF Unified Multi-Carrier Rating API

	Endpoint:
	    POST /api/method/ltl_quote.api.quote.get_ltl_rates

	Request body (JSON):
	    {
	        "carrier_preference": "Dayton Freight",
	        "origin_zip": "45414",
	        "destination_zip": "60601",
	        "accessorial_codes": ["LIFTGATE", "RESIDENTIAL"],
	        "items": [{"classification": "70", "weight": 1450, "qty": 1}]
	    }
	"""
	# 1. Capture request context safely from Postman / REST clients
	headers = dict(frappe.request.headers) if hasattr(frappe, "request") else {}
	if hasattr(frappe, "request") and frappe.request.data:
		try:
			body = json.loads(frappe.request.data.decode("utf-8"))
		except (ValueError, UnicodeDecodeError):
			body = dict(frappe.local.form_dict)
	else:
		body = dict(frappe.local.form_dict)
	body.pop("cmd", None)

	status = "Queued"
	response_payload = {}
	carrier_id = "DAYTON" if "Dayton" in body.get("carrier_preference", "") else "MOCK"

	try:
		request = parse_rating_payload(payload or body, **kwargs)
		for field in ("origin_city", "origin_state", "destination_city", "destination_state"):
			if body.get(field):
				request[field] = str(body[field]).strip()
		body = {**body, **request}

		raw_preference = request.get("carrier_preference") or ""
		if raw_preference:
			carrier_id = resolve_carrier_id(raw_preference) or carrier_id
		else:
			carrier_id = None

		carrier_docs, _log_label = load_carrier_for_rating(carrier_id)
		if carrier_id and carrier_id != "MOCK":
			carrier_doc = carrier_docs[0]
			body["carrier_id"] = carrier_doc.name
			body["carrier_code"] = carrier_doc.carrier_code
			body["carrier_name"] = carrier_doc.carrier_name

		quote_request = _create_quote_request(request)

		shipment_request = _build_shipment_request_from_payload(request)

		aggregator = RateAggregator(
			quote_request,
			carrier_ids=[carrier_id] if carrier_id else None,
			shipment_request=shipment_request,
		)
		aggregator.resolved_carriers = carrier_docs
		aggregation = aggregator.aggregate(timeout=request.get("timeout") or None)
		ranked_quotes = rank_quotes(aggregation.get("raw_quotes") or [])

		errors = _public_rate_errors(aggregation.get("errors") or [])

		_enrich_ranked_quotes_from_doc(quote_request, ranked_quotes)

		api_status = "success" if ranked_quotes else "error"
		response_payload = {
			"status": api_status,
			"quote_request_id": quote_request.name,
			"carrier_id": carrier_id,
			"summary": {
				"total_carriers_pinged": aggregation.get("carriers_pinged", 0),
				"successful_quotes": len(ranked_quotes),
				"failed_quotes": len(errors),
			},
			"data": {
				"origin_zip": request["origin_zip"],
				"destination_zip": request["destination_zip"],
				"weight": request["total_weight"],
				"freight_class": request["freight_class"],
				"quotes": ranked_quotes,
			},
			"errors": errors,
			"recommendations": aggregation.get("recommendations") or {},
		}

		if ranked_quotes:
			status = "Quotes Received"
		elif errors:
			status = "API Error"
		else:
			status = "No Quotes Received"

	except frappe.DoesNotExistError as db_err:
		status = "API Error"
		response_payload = {
			"status": "error",
			"error": f"Configuration Missing: {db_err}",
			"carrier_id": carrier_id,
		}
	except frappe.ValidationError as e:
		status = "API Error"
		response_payload = {"status": "error", "error": str(e)}
	except Exception as e:
		frappe.log_error(message=frappe.get_traceback(), title="LTL get_ltl_rates API Error")
		status = "Connection Failed" if "timeout" in str(e).lower() else "API Error"
		raw = str(e)
		if is_auth_error_text(raw):
			raw = "Could not refresh carrier rates. Please try again."
		response_payload = {"status": "error", "error": raw, "carrier_id": carrier_id}
	finally:
		log_carrier_id = carrier_id or ("DAYTON" if "Dayton" in body.get("carrier_preference", "") else "Multi-Carrier")
		log_api_transaction(headers, body, response_payload, status, log_carrier_id)

	return response_payload


def _build_shipment_request_from_payload(request: dict) -> ShipmentRequest:
	"""Map parsed Postman/API payload directly into ShipmentRequest for carrier adapters."""
	accessorials = build_accessorial_items_from_payload(request.get("accessorial_rows") or [])
	if not accessorials:
		codes = request.get("accessorial_codes") or request.get("accessorials") or []
		accessorials = build_accessorial_items_from_payload(
			[{"code": code} for code in codes] if codes else []
		)

	apply_default_handling_dimensions(request)
	items = [item for item in (request.get("items") or []) if isinstance(item, dict)]
	return ShipmentRequest(
		origin_zip=request["origin_zip"],
		origin_city=request.get("origin_city") or "",
		origin_state=request.get("origin_state") or "",
		destination_zip=request["destination_zip"],
		destination_city=request.get("destination_city") or "",
		destination_state=request.get("destination_state") or "",
		total_weight=request["total_weight"],
		freight_class=request["freight_class"],
		length=float(request.get("length") or 0),
		width=float(request.get("width") or 0),
		height=float(request.get("height") or 0),
		pieces=int(request.get("pieces") or 1),
		accessorials=accessorials,
		items=items,
		payment_terms=str(request.get("payment_terms") or request.get("terms") or "Prepaid"),
		payment_payer=str(request.get("payment_payer") or request.get("payer") or "Shipper"),
	)


def _public_rate_errors(errors: list) -> list[dict]:
	"""Keep TForce auth failures visible; hide other connectors' credential noise."""
	if errors and not isinstance(errors[0], dict):
		errors = [{"carrier": "unknown", "error": err} for err in errors]
	visible = []
	for err in errors:
		if not isinstance(err, dict):
			err = {"carrier": "unknown", "error": str(err)}
		carrier = err.get("carrier") or ""
		message = err.get("error") or ""
		if is_auth_error_text(message) and not is_tforce_connector_text(carrier, message):
			continue
		if is_auth_error_text(message) and is_tforce_connector_text(carrier, message):
			visible.append(
				{
					**err,
					"error": TFORCE_AUTH_USER_MESSAGE,
				}
			)
			continue
		visible.append(err)
	return visible


def _create_quote_request(request: dict):
	line_items = _map_request_line_items(request.get("items") or [])
	doc = frappe.get_doc(
		{
			"doctype": "LTL Quote Request",
			"naming_series": "LTL-QR-.YYYY.-",
			"origin_zip": request["origin_zip"],
			"origin_city": request.get("origin_city") or "",
			"origin_state": request.get("origin_state") or "",
			"destination_zip": request["destination_zip"],
			"destination_city": request.get("destination_city") or "",
			"destination_state": request.get("destination_state") or "",
			"shipper_company_name": request.get("shipper_company_name") or request.get("shipper_name") or "",
			"shipper_address": request.get("shipper_address") or "",
			"consignee_company_name": request.get("consignee_company_name")
			or request.get("consignee_name")
			or "",
			"consignee_address": request.get("consignee_address") or "",
			"contact_name": request.get("contact_name") or request.get("origin_contact_name") or "",
			"contact_phone": request.get("contact_phone") or request.get("origin_contact_phone") or "",
			"origin_contact_email": request.get("origin_contact_email") or request.get("contact_email") or "",
			"destination_contact_name": request.get("destination_contact_name")
			or request.get("consignee_contact_name")
			or "",
			"destination_contact_phone": request.get("destination_contact_phone")
			or request.get("consignee_contact_phone")
			or "",
			"destination_contact_email": request.get("destination_contact_email") or "",
			"total_weight": request["total_weight"],
			"freight_class": request["freight_class"],
			"length": request.get("length") or 0,
			"width": request.get("width") or 0,
			"height": request.get("height") or 0,
			"pieces": request.get("pieces") or 1,
			"requested_on": now_datetime(),
			"status": "Draft",
			"accessorials": request.get("accessorial_rows") or [],
			"line_items": line_items,
		}
	)

	if request.get("save_request", True):
		doc.insert(ignore_permissions=True)

	return doc


def _map_request_line_items(items: list) -> list[dict]:
	"""Map rating/UI `items` array into LTL Quote Request Line Item rows."""
	rows = []
	for item in items or []:
		if not isinstance(item, dict):
			continue
		description = (
			item.get("description")
			or item.get("commodity_description")
			or item.get("item_name")
			or ""
		)
		freight_class = line_item_freight_class(item)
		nmfc = item.get("nmfc") or item.get("nmfc_number") or ""
		qty = item.get("quantity") if item.get("quantity") not in (None, "") else item.get("qty")
		hazmat_raw = item.get("hazmat")
		if hazmat_raw in (None, ""):
			hazmat_raw = item.get("hazardous")
		length, width, height = default_handling_dimensions(
			item.get("length"), item.get("width"), item.get("height")
		)
		rows.append(
			{
				"item_number": item.get("item_number") or "",
				"item_name": item.get("item_name") or "",
				"item_id": item.get("item_id") or "",
				"description": description,
				"quantity": cint(qty or 1),
				"units": item.get("units") or "",
				"packaging_units": item.get("packaging_units") or "",
				"packaging_unit_count": cint(item.get("packaging_unit_count") or 0) or None,
				"rate": flt(item.get("rate") or 0) or None,
				"freight_class": str(freight_class or ""),
				"nmfc": str(nmfc or ""),
				"hazmat": 1 if hazmat_raw in (True, 1, "1", "true", "True", "yes", "Y") else 0,
				"weight": flt(item.get("weight") or 0) or None,
				"weight_unit": item.get("weight_unit") or item.get("weight_units") or "LBS",
				"length": length,
				"width": width,
				"height": height,
				"dimension_unit": item.get("dimension_unit") or item.get("dimension_units") or "IN",
				"volume": flt(item.get("volume") or 0) or None,
				"volume_units": item.get("volume_units") or "",
				"area": flt(item.get("area") or 0) or None,
				"area_units": item.get("area_units") or "",
				"linear_feet": flt(item.get("linear_feet") or 0) or None,
				"hazmat_class_division": item.get("hazmat_class_division") or "",
				"hazmat_phone": item.get("hazmat_phone") or "",
				"hazmat_contact_company": item.get("hazmat_contact_company") or "",
				"hazmat_contact": item.get("hazmat_contact") or "",
				"hazmat_number": item.get("hazmat_number") or "",
				"hazmat_packaging_group": item.get("hazmat_packaging_group") or "",
				"hazmat_number_type": item.get("hazmat_number_type") or "",
				"pickup_stop_location": item.get("pickup_stop_location") or "",
				"pickup": item.get("pickup") or "",
				"drop_stop_location": item.get("drop_stop_location") or "",
				"drop": item.get("drop") or "",
			}
		)
	return rows


def _enrich_ranked_quotes_from_doc(quote_request, ranked_quotes: list[dict]):
	rows_by_carrier = {row.carrier: row for row in (quote_request.carrier_quotes or [])}
	for quote in ranked_quotes:
		row = rows_by_carrier.get(quote.get("carrier_code"))
		if not row:
			continue
		if row.estimated_delivery_date:
			quote["estimated_delivery_date"] = str(row.estimated_delivery_date)


@frappe.whitelist(allow_guest=False)
def book_shipment(quote_request: str, quote_row_idx: int = 0) -> dict:
	"""Book shipment from an existing FLOWWOLF quote request."""
	doc = frappe.get_doc("LTL Quote Request", quote_request)
	doc.selected_carrier_quote = str(quote_row_idx)
	result = doc.book_selected_quote()
	return {
		"status": "success",
		"quote_request_id": quote_request,
		"data": result,
	}


@frappe.whitelist(allow_guest=False)
def track_shipment(shipment: str) -> dict:
	"""Refresh tracking for a booked shipment."""
	doc = frappe.get_doc("LTL Shipment", shipment)
	result = doc.refresh_tracking()
	return {
		"status": "success",
		"shipment": shipment,
		"data": result,
	}


@frappe.whitelist(allow_guest=False)
def get_quote_booking_context(quote_request_id: str) -> dict:
	"""Return booking state for a quote request so the dashboard can block re-booking."""
	if not quote_request_id or not frappe.db.exists("LTL Quote Request", quote_request_id):
		return {"is_booked": False}

	doc = frappe.db.get_value(
		"LTL Quote Request",
		quote_request_id,
		["name", "status", "final_carrier", "bol_number", "bol_document_url"],
		as_dict=True,
	)
	shipment = frappe.db.get_value("LTL Shipment", {"quote_request": quote_request_id}, "name")
	is_booked = bool(shipment) or doc.status == "Booked"

	bol_url = resolve_shipment_bol_url(shipment_name=shipment, quote_request=quote_request_id)
	bol_image = resolve_shipment_bol_image_url(shipment_name=shipment)
	booked_carrier = doc.final_carrier or ""
	if not booked_carrier and shipment:
		booked_carrier = frappe.db.get_value("LTL Shipment", shipment, "carrier") or ""

	return {
		"is_booked": is_booked,
		"quote_status": doc.status,
		"shipment": shipment,
		"booked_carrier": booked_carrier,
		"bol_url": bol_url,
		"bol_image": bol_image,
		"bol_number": doc.bol_number or "",
	}


@frappe.whitelist(allow_guest=False)
def accept_carrier_quote(quote_request_id, carrier_code, total_charge, carrier_quote_id, items=None):
	"""
	Save the selected rate, transition status to Accepted, and trigger the
	carrier's electronic BOL gateway API (ArcBest XML or Dayton eBOL).

	Optional ``items`` (JSON list or list of dicts) upserts quote-request line
	items before booking so the BOL gets UI commodities even if rates were
	fetched without them.
	"""
	try:
		doc = frappe.get_doc("LTL Quote Request", quote_request_id)
		carrier_key = resolve_carrier_id(carrier_code) or str(carrier_code or "").upper()

		existing_shipment = frappe.db.get_value("LTL Shipment", {"quote_request": quote_request_id}, "name")
		if existing_shipment or doc.status == "Booked":
			shipment_name = existing_shipment
			bol_url = resolve_shipment_bol_url(shipment_name=shipment_name, quote_request=doc)
			return {
				"status": "already_booked",
				"message": "This quote has already been booked.",
				"quote_request_id": doc.name,
				"shipment": shipment_name,
				"booked_carrier": doc.final_carrier or carrier_key,
				"bol_number": doc.bol_number or "",
				"pro_number": doc.pro_number or "",
				"bol_document_url": bol_url,
				"bol_image": resolve_shipment_bol_image_url(shipment_name=shipment_name),
				"data": {"shipment": shipment_name} if shipment_name else {},
			}

		_upsert_quote_request_line_items(doc, items)

		doc.status = "Accepted"
		doc.final_carrier = carrier_key
		doc.final_charge = flt(total_charge)
		doc.carrier_reference_number = str(carrier_quote_id or "")
		doc.save(ignore_permissions=True)

		enrich_location_fields(doc, "origin")
		enrich_location_fields(doc, "destination")

		automated = carrier_key in ("ARCB", "ARCBEST", "DAYTON", "TFORCE", "MOCK", "SMC3") or str(
			carrier_code
		).upper() in (
			"ARCB",
			"ARCBEST",
			"DAYTON",
			"TFORCE",
			"TFF",
			"MOCK",
			"SMC3",
		) or str(carrier_code or "").upper().startswith("SMC3-")
		shipment_name = None

		if automated:
			from ltl_quote.api.flowwolf import _book_quote_core

			# Ensure quote lines exist; if missing, synthesize a row so executor can book.
			if not doc.carrier_quotes:
				doc.append(
					"carrier_quotes",
					{
						"carrier": carrier_key if frappe.db.exists("LTL Carrier", carrier_key) else "ARCB",
						"carrier_name": carrier_code,
						"carrier_quote_id": carrier_quote_id,
						"status": "Received",
						"total_charge": flt(total_charge),
						"currency": get_quote_currency(),
					},
				)
				doc.save(ignore_permissions=True)

			booking = _book_quote_core(
				quote_request_id=doc.name,
				carrier_code=carrier_code,
				carrier_quote_id=carrier_quote_id,
				is_test=False,
			)
			shipment_name = booking.get("shipment")
			doc.reload()
		else:
			doc.add_comment(
				text=f"Quote accepted for {carrier_code}. No automated BOL gateway configured for this carrier."
			)
			doc.save(ignore_permissions=True)
			frappe.db.commit()

		bol_url = resolve_shipment_bol_url(shipment_name=shipment_name, quote_request=doc)
		return {
			"status": "success",
			"message": f"Quote accepted and processed for {carrier_code} completely.",
			"quote_request_id": doc.name,
			"shipment": shipment_name,
			"booked_carrier": carrier_key,
			"data": {"shipment": shipment_name} if shipment_name else {},
			"bol_number": doc.bol_number if getattr(doc, "bol_number", None) else "Pending",
			"pro_number": doc.pro_number if getattr(doc, "pro_number", None) else "Auto-Assigned",
			"bol_document_url": bol_url or (doc.bol_document_url if getattr(doc, "bol_document_url", None) else ""),
			"bol_image": resolve_shipment_bol_image_url(shipment_name=shipment_name),
		}

	except Exception as e:
		frappe.log_error(title="Quote Booking Pipeline Failure", message=frappe.get_traceback())
		return {"status": "failed", "error": str(e)}


def _upsert_quote_request_line_items(doc, items) -> None:
	"""Replace quote request line_items from UI/API payload when provided."""
	if items in (None, "", []):
		return

	if isinstance(items, str):
		try:
			items = json.loads(items)
		except (TypeError, ValueError):
			frappe.throw("Invalid items payload; expected a JSON list of line items.")

	if not isinstance(items, list) or not items:
		return

	mapped = _map_request_line_items(items)
	if not mapped:
		return

	doc.set("line_items", [])
	for row in mapped:
		doc.append("line_items", row)

	# Keep top-level freight aggregates aligned with commodities when present.
	first = mapped[0]
	if first.get("freight_class"):
		doc.freight_class = first["freight_class"]
	if first.get("length") and first.get("width") and first.get("height"):
		doc.length = first["length"]
		doc.width = first["width"]
		doc.height = first["height"]
	weights = [flt(r.get("weight") or 0) * max(cint(r.get("quantity") or 1), 1) for r in mapped]
	total_weight = sum(weights)
	if total_weight > 0:
		doc.total_weight = total_weight
	pieces = sum(max(cint(r.get("quantity") or 1), 1) for r in mapped)
	if pieces > 0:
		doc.pieces = pieces


def _route_arcbest_bol(doc, carrier_quote_id, total_charge=0):
	"""Thin wrapper: ArcBest BOL via adapter + shipment create (legacy accept path)."""
	try:
		carrier_doc = frappe.get_doc("LTL Carrier", "ARCB") if frappe.db.exists("LTL Carrier", "ARCB") else None
		if not carrier_doc:
			return {
				"status": "failed",
				"message": "ArcBest carrier record (ARCB) not found.",
				"quote_request_id": doc.name,
				"bol_number": "Failed",
				"pro_number": "Failed",
				"bol_document_url": "",
			}

		from ltl_quote.utils.booking import resolve_shipper_context as _shipper

		adapter = get_adapter(carrier_doc)
		shipper = _shipper(quote_request=doc)
		booking_payload = {
			"carrier_quote_id": carrier_quote_id,
			"total_charge": flt(total_charge),
			"origin_zip": doc.origin_zip,
			"destination_zip": doc.destination_zip,
			"origin_city": doc.origin_city,
			"origin_state": doc.origin_state,
			"destination_city": doc.destination_city,
			"destination_state": doc.destination_state,
			"total_weight": doc.total_weight,
			"pieces": doc.pieces or 1,
			"freight_class": doc.freight_class,
			"quote_request": doc.name,
			"shipper_name": shipper["shipper_name"],
			"shipper_address": shipper["shipper_address"],
			"consignee_name": shipper["consignee_name"],
			"consignee_address": shipper["consignee_address"],
			"contact_name": shipper["contact_name"],
			"contact_phone": shipper["contact_phone"],
			"is_test": False,
		}
		bol_result = adapter.book_shipment(booking_payload)
		doc.bol_document_url = bol_result.get("bol_document_url") or ""
		doc.bol_number = bol_result.get("bol_number") or str(carrier_quote_id)
		doc.pro_number = bol_result.get("pro_number") or "Auto-Assigned"
		if doc.bol_document_url:
			doc.add_comment(
				text=(
					f"<b>ArcBest BOL Generated!</b><br>"
					f"<a href='{doc.bol_document_url}' target='_blank'>Download PDF</a>"
				)
			)
		shipment_name = _create_arcbest_shipment(doc, total_charge)
		return {"shipment": shipment_name}
	except Exception as exc:
		frappe.log_error(title="ArcBest Parsing Gateway Issue", message=frappe.get_traceback())
		return {
			"status": "failed",
			"message": f"ArcBest API Rejected: {exc}",
			"quote_request_id": doc.name,
			"bol_number": "Failed",
			"pro_number": "Failed",
			"bol_document_url": "",
		}


def _find_arcbest_xml_node(root, tag_name: str):
	"""Locate an ArcBest XML node case-insensitively (delegates to adapter helpers)."""
	from ltl_quote.carrier_network.adapters.arcbest import ArcBestCarrierAdapter

	return ArcBestCarrierAdapter._find_xml_node(root, tag_name)


def _arcbest_xml_text(root, tag_name: str, default: str = "") -> str:
	from ltl_quote.carrier_network.adapters.arcbest import ArcBestCarrierAdapter

	return ArcBestCarrierAdapter._xml_text(root, tag_name, default)


def _extract_arcbest_error_message(root) -> str:
	from ltl_quote.carrier_network.adapters.arcbest import ArcBestCarrierAdapter

	return ArcBestCarrierAdapter._extract_error_message(root)


def _create_arcbest_shipment(doc, total_charge=0) -> str | None:
	"""Create a linked LTL Shipment for a successful ArcBest BOL, mirroring Dayton lifecycle."""
	existing = frappe.db.get_value("LTL Shipment", {"quote_request": doc.name}, "name")
	if existing:
		doc.status = "Booked"
		return existing

	arcb_row = next(
		(row for row in (doc.carrier_quotes or []) if row.carrier in ("ARCB", "ARCBEST")),
		None,
	)
	carrier_name = "ArcBest Freight"
	if frappe.db.exists("LTL Carrier", "ARCB"):
		carrier_name = frappe.db.get_value("LTL Carrier", "ARCB", "carrier_name") or carrier_name

	shipment = frappe.get_doc(
		{
			"doctype": "LTL Shipment",
			"quote_request": doc.name,
			"carrier": "ARCB",
			"status": "Booked",
			"booked_on": now_datetime(),
			"bol_number": doc.bol_number,
			"pro_number": doc.pro_number,
			"carrier_confirmation": doc.bol_number,
			"total_charge": flt(total_charge) or doc.final_charge or (arcb_row.total_charge if arcb_row else 0),
			"currency": (arcb_row.currency if arcb_row else None) or get_quote_currency(),
			"transit_days": arcb_row.transit_days if arcb_row else None,
			"estimated_delivery_date": arcb_row.estimated_delivery_date if arcb_row else None,
			"dispatch_status": "Pending",
			"current_status": "Booked",
		}
	)
	if doc.bol_document_url:
		shipment.bol_document = doc.bol_document_url
		shipment.bol_document_url = doc.bol_document_url

	shipment.insert(ignore_permissions=True)
	doc.status = "Booked"
	return shipment.name


def _create_dayton_shipment(doc, total_charge, carrier_quote_id, bol_result=None, booking_payload=None) -> str | None:
	"""Create a linked LTL Shipment after a successful Dayton eBOL."""
	existing = frappe.db.get_value("LTL Shipment", {"quote_request": doc.name}, "name")
	if existing:
		doc.status = "Booked"
		return existing

	bol_result = bol_result or {}
	dayton_row = next(
		(row for row in (doc.carrier_quotes or []) if row.carrier == "DAYTON"),
		None,
	)

	shipment = frappe.get_doc(
		{
			"doctype": "LTL Shipment",
			"quote_request": doc.name,
			"carrier": "DAYTON",
			"status": "Booked",
			"booked_on": now_datetime(),
			"bol_number": doc.bol_number or bol_result.get("bol_number"),
			"pro_number": doc.pro_number or bol_result.get("pro_number"),
			"dayton_bol_id": bol_result.get("dayton_bol_id"),
			"carrier_confirmation": bol_result.get("carrier_confirmation") or doc.bol_number,
			"total_charge": flt(total_charge) or doc.final_charge or (dayton_row.total_charge if dayton_row else 0),
			"currency": (dayton_row.currency if dayton_row else None) or get_quote_currency(),
			"transit_days": dayton_row.transit_days if dayton_row else None,
			"estimated_delivery_date": dayton_row.estimated_delivery_date if dayton_row else None,
			"dispatch_status": "Pending",
			"current_status": "Booked",
		}
	)
	if doc.bol_document_url:
		shipment.bol_document = doc.bol_document_url
		shipment.bol_document_url = doc.bol_document_url

	shipment.insert(ignore_permissions=True)

	bol_file_url = shipment.bol_document or shipment.bol_document_url
	if booking_payload and not shipment.bol_document:
		from ltl_quote.carrier_network.adapters.dayton import attach_dayton_bol_to_shipment

		res = attach_dayton_bol_to_shipment(shipment, booking_payload, bol_result=bol_result)
		if res.get("status") == "success" and res.get("document_url"):
			shipment.reload()
			shipment.bol_number = res.get("bol_number") or shipment.bol_number
			shipment.pro_number = res.get("pro_number") or shipment.pro_number
			shipment.bol_document = res["document_url"]
			shipment.bol_document_url = res["document_url"]
			shipment.save(ignore_permissions=True)
	elif booking_payload:
		from ltl_quote.carrier_network.adapters.dayton import sync_dayton_bol_details_to_shipment

		sync_dayton_bol_details_to_shipment(
			shipment.name,
			booking_payload,
			bol_result=bol_result,
			bol_file_url=bol_file_url,
		)

	doc.status = "Booked"
	return shipment.name


def _route_dayton_bol(doc, carrier_quote_id, total_charge):
	"""Thin wrapper: Dayton eBOL via adapter (legacy accept path)."""
	try:
		from ltl_quote.carrier_network.adapters.dayton import resolve_dayton_bol_download

		carrier_doc = frappe.get_doc("LTL Carrier", "DAYTON")
		adapter = get_adapter(carrier_doc)

		origin_city, origin_state = resolve_us_location(doc.origin_zip, doc.origin_city, doc.origin_state)
		destination_city, destination_state = resolve_us_location(
			doc.destination_zip, doc.destination_city, doc.destination_state
		)
		shipper = resolve_shipper_context(quote_request=doc)
		contact_email = (
			getattr(doc, "origin_contact_email", None)
			or getattr(doc, "contact_email", None)
			or frappe.db.get_value("User", frappe.session.user, "email")
		)

		booking_payload = {
			"carrier_quote_id": carrier_quote_id,
			"total_charge": flt(total_charge),
			"origin_zip": doc.origin_zip,
			"destination_zip": doc.destination_zip,
			"total_weight": doc.total_weight,
			"pieces": doc.pieces or 1,
			"length": getattr(doc, "length", None),
			"width": getattr(doc, "width", None),
			"height": getattr(doc, "height", None),
			"dimension_uom": getattr(doc, "dimension_uom", None) or "IN",
			"origin_city": origin_city,
			"origin_state": origin_state,
			"destination_city": destination_city,
			"destination_state": destination_state,
			"quote_request": doc.name,
			"freight_class": doc.freight_class,
			"shipper_name": shipper["shipper_name"],
			"shipper_address": shipper["shipper_address"],
			"consignee_name": shipper["consignee_name"],
			"consignee_address": shipper["consignee_address"],
			"contact_name": shipper["contact_name"],
			"contact_phone": shipper["contact_phone"],
			"origin_contact_name": getattr(doc, "contact_name", None) or shipper["contact_name"],
			"origin_contact_phone": getattr(doc, "contact_phone", None) or shipper["contact_phone"],
			"contact_email": contact_email,
			"origin_contact_email": contact_email,
			"destination_contact_name": getattr(doc, "destination_contact_name", None),
			"destination_contact_phone": getattr(doc, "destination_contact_phone", None),
			"destination_contact_email": getattr(doc, "destination_contact_email", None),
			"accessorials": [
				{
					"accessorial_code": getattr(row, "accessorial_code", None),
					"service_group": getattr(row, "service_group", None),
					"quantity": getattr(row, "quantity", 1) or 1,
				}
				for row in (doc.accessorials or [])
			],
			"items": _map_request_line_items(
				[
					{
						"item_number": getattr(row, "item_number", None),
						"item_name": getattr(row, "item_name", None),
						"item_id": getattr(row, "item_id", None),
						"description": getattr(row, "description", None),
						"quantity": getattr(row, "quantity", None),
						"units": getattr(row, "units", None),
						"packaging_units": getattr(row, "packaging_units", None),
						"packaging_unit_count": getattr(row, "packaging_unit_count", None),
						"rate": getattr(row, "rate", None),
						"freight_class": getattr(row, "freight_class", None),
						"nmfc": getattr(row, "nmfc", None),
						"hazmat": getattr(row, "hazmat", None),
						"weight": getattr(row, "weight", None),
						"weight_unit": getattr(row, "weight_unit", None),
						"length": getattr(row, "length", None),
						"width": getattr(row, "width", None),
						"height": getattr(row, "height", None),
						"dimension_unit": getattr(row, "dimension_unit", None),
					}
					for row in (doc.line_items or [])
				]
			),
		}
		if booking_payload["items"]:
			first = booking_payload["items"][0]
			booking_payload["commodity_description"] = first.get("description") or ""
			booking_payload["nmfc"] = first.get("nmfc") or ""
			booking_payload["is_hazardous"] = bool(first.get("hazmat"))

		bol_result = adapter.book_shipment(booking_payload)
		doc.bol_number = bol_result.get("bol_number") or doc.bol_number
		doc.pro_number = bol_result.get("pro_number") or doc.pro_number

		download = resolve_dayton_bol_download(booking_payload, bol_result=bol_result)
		if download.get("status") == "success" and download.get("document_url"):
			doc.bol_document_url = download["document_url"]

		doc.add_comment(
			text=(
				f"<b>Dayton Freight eBOL Confirmed Successfully</b><br>"
				f"BOL #: {doc.bol_number}<br>"
				f"PRO #: {doc.pro_number}"
				+ (
					f"<br><a href='{doc.bol_document_url}' target='_blank' "
					f"class='btn btn-xs btn-primary' style='margin-top: 5px; color: #fff;'>"
					f"Download BOL PDF</a>"
					if doc.bol_document_url
					else ""
				)
			)
		)
		return {"bol_result": bol_result, "booking_payload": booking_payload}

	except Exception:
		frappe.log_error(title="Dayton BOL Generation Failure", message=frappe.get_traceback())
		doc.add_comment(text="Rate saved locally, but Dayton eBOL gateway processing failed.")
		return None


def _get_arcbest_api_id(carrier_doc) -> str:
	from ltl_quote.carrier_network.adapters.arcbest import ArcBestCarrierAdapter, DEFAULT_API_ID

	if carrier_doc:
		return ArcBestCarrierAdapter(carrier_doc)._get_api_id()
	return DEFAULT_API_ID


def _normalize_arcbest_quote_id(carrier_quote_id) -> str:
	from ltl_quote.carrier_network.adapters.arcbest import ArcBestCarrierAdapter

	return ArcBestCarrierAdapter._normalize_quote_id(carrier_quote_id)


def _resolve_arcbest_bol_quote_id(carrier_quote_id, doc) -> str:
	from ltl_quote.carrier_network.adapters.arcbest import ArcBestCarrierAdapter

	adapter = ArcBestCarrierAdapter()
	return adapter._resolve_bol_quote_id(
		{"carrier_quote_id": carrier_quote_id, "quote_request": getattr(doc, "name", None)}
	)
