# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""
FLOWWOLF Unified Multi-Carrier Rating API

Single gateway endpoint that accepts one standard payload, fans out to all
enabled carrier adapters in parallel, merges responses, ranks quotes, and
returns a normalized JSON schema (BlueShip / project44 style).
"""

import frappe
from frappe.utils import now_datetime

from ltl_quote.api.payload import parse_rating_payload
from ltl_quote.decision_engine.recommender import rank_quotes
from ltl_quote.rate_engine.aggregator import RateAggregator


@frappe.whitelist(allow_guest=False)
def get_ltl_rates(payload=None, **kwargs):
	"""
	FLOWWOLF Unified Multi-Carrier Rating API

	Endpoint:
	    POST /api/method/ltl_quote.api.quote.get_ltl_rates

	Request body (JSON):
	    {
	        "origin_zip": "90210",
	        "destination_zip": "60601",
	        "weight": 1250,
	        "freight_class": "70",
	        "accessorials": ["Lift Gate Delivery", "Residential Delivery"],
	        "timeout": 8
	    }

	Legacy parameters (still supported):
	    origin_zip, destination_zip, total_weight, freight_class, accessorials, ...
	"""
	request = parse_rating_payload(payload, **kwargs)
	quote_request = _create_quote_request(request)

	aggregation = RateAggregator(quote_request).aggregate(timeout=request.get("timeout") or None)
	ranked_quotes = rank_quotes(aggregation.get("raw_quotes") or [])

	errors = aggregation.get("errors") or []
	if errors and not isinstance(errors[0], dict):
		errors = [{"carrier": "unknown", "error": err} for err in errors]

	_enrich_ranked_quotes_from_doc(quote_request, ranked_quotes)

	status = "success" if ranked_quotes else "error"
	return {
		"status": status,
		"quote_request_id": quote_request.name,
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


def _create_quote_request(request: dict):
	doc = frappe.get_doc(
		{
			"doctype": "LTL Quote Request",
			"naming_series": "LTL-QR-.YYYY.-",
			"origin_zip": request["origin_zip"],
			"destination_zip": request["destination_zip"],
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
