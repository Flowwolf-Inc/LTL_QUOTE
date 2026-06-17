"""
FLOWWOLF Unified Multi-Carrier Rating API

Single gateway endpoint that accepts one standard payload, fans out to all
enabled carrier adapters in parallel, merges responses, ranks quotes, and
returns a normalized JSON schema (BlueShip / project44 style).
"""

import json

import frappe
from frappe.utils import now_datetime

from ltl_quote.api.carrier_mapping import load_carrier_for_rating, resolve_carrier_id
from ltl_quote.api.payload import parse_rating_payload
from ltl_quote.carrier_network.accessorials import build_accessorial_items_from_payload
from ltl_quote.carrier_network.adapters.base import ShipmentRequest
from ltl_quote.decision_engine.recommender import rank_quotes
from ltl_quote.rate_engine.aggregator import RateAggregator
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

		errors = aggregation.get("errors") or []
		if errors and not isinstance(errors[0], dict):
			errors = [{"carrier": "unknown", "error": err} for err in errors]

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
		response_payload = {"status": "error", "error": str(e), "carrier_id": carrier_id}
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
	)


def _create_quote_request(request: dict):
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
			"total_weight": request["total_weight"],
			"freight_class": request["freight_class"],
			"length": request.get("length") or 0,
			"width": request.get("width") or 0,
			"height": request.get("height") or 0,
			"pieces": request.get("pieces") or 1,
			"requested_on": now_datetime(),
			"status": "Draft",
			"accessorials": request.get("accessorial_rows") or [],
		}
	)

	if request.get("save_request", True):
		doc.insert(ignore_permissions=True)

	return doc


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
