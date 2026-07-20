"""
FlowWolf Unified Multi-Carrier LTL Rating + BOL Gateway API

POST /api/method/ltl_quote.api.flowwolf.get_rates
POST /api/method/ltl_quote.api.flowwolf.create_bol
GET/POST /api/method/ltl_quote.api.flowwolf.get_shipment_details
POST /api/method/ltl_quote.api.flowwolf.track_by_number
POST /api/method/ltl_quote.api.flowwolf.track_history
POST /api/method/ltl_quote.api.flowwolf.track_by_date
POST /api/method/ltl_quote.api.flowwolf.track_pending
"""

from __future__ import annotations

import json

import frappe
from frappe.utils import add_days, flt, getdate, now_datetime

from ltl_quote.api.carrier_mapping import load_carrier_for_rating, resolve_carrier_id
from ltl_quote.api.payload import parse_rating_payload
from ltl_quote.api.quote import (
	_build_shipment_request_from_payload,
	_create_quote_request,
	_upsert_quote_request_line_items,
)
from ltl_quote.booking.executor import ShipmentExecutor
from ltl_quote.carrier_network.registry import get_adapter
from ltl_quote.decision_engine.recommender import rank_quotes
from ltl_quote.utils.booking import resolve_shipment_bol_url
from ltl_quote.utils.currency import get_quote_currency
from ltl_quote.utils.location import enrich_location_fields, resolve_us_location
from ltl_quote.utils.transaction_log import log_api_transaction

FLOWWOLF_RATES_ENDPOINT = "/api/method/ltl_quote.api.flowwolf.get_rates"
FLOWWOLF_BOL_ENDPOINT = "/api/method/ltl_quote.api.flowwolf.create_bol"
FLOWWOLF_SHIPMENT_DETAILS_ENDPOINT = "/api/method/ltl_quote.api.flowwolf.get_shipment_details"
FLOWWOLF_TRACK_BY_NUMBER_ENDPOINT = "/api/method/ltl_quote.api.flowwolf.track_by_number"
FLOWWOLF_TRACK_HISTORY_ENDPOINT = "/api/method/ltl_quote.api.flowwolf.track_history"
FLOWWOLF_TRACK_BY_DATE_ENDPOINT = "/api/method/ltl_quote.api.flowwolf.track_by_date"
FLOWWOLF_TRACK_PENDING_ENDPOINT = "/api/method/ltl_quote.api.flowwolf.track_pending"
FLOWWOLF_ENGINE = "FlowWolf Aggregator Engine v1"
FLOWWOLF_API_ENDPOINT = FLOWWOLF_RATES_ENDPOINT


@frappe.whitelist(allow_guest=False)
def get_rates(payload=None, **kwargs):
	"""FlowWolf unified multi-carrier LTL rating gateway."""
	headers, body = _read_request_context()
	status = "Queued"
	response_payload: dict = {}
	carrier_id = None

	try:
		request = parse_rating_payload(payload or body, **kwargs)
		for field in ("origin_city", "origin_state", "destination_city", "destination_state"):
			if body.get(field):
				request[field] = str(body[field]).strip()
		body = {**body, **request}

		shipment_request = _build_shipment_request_from_payload(request)

		raw_preference = request.get("carrier_preference") or ""
		carrier_id = resolve_carrier_id(raw_preference) if raw_preference else None
		carrier_docs, _ = load_carrier_for_rating(carrier_id)

		quote_request = _create_quote_request({**request, "save_request": request.get("save_request", True)})
		aggregated_quotes, errors = _broadcast_carrier_rates(carrier_docs, shipment_request)
		_persist_carrier_quotes(quote_request, aggregated_quotes, errors)
		ranked_quotes = rank_quotes(aggregated_quotes)

		recommendations = _build_flowwolf_recommendations(ranked_quotes)
		response_payload = {
			"status": "success" if ranked_quotes else "error",
			"engine": FLOWWOLF_ENGINE,
			"quote_request_id": quote_request.name,
			"summary": {
				"total_carriers_pinged": len(carrier_docs),
				"successful_quotes": len(ranked_quotes),
				"failed_quotes": len(errors),
			},
			"data": {
				"origin_zip": shipment_request.origin_zip,
				"destination_zip": shipment_request.destination_zip,
				"weight": shipment_request.total_weight,
				"freight_class": shipment_request.freight_class,
				"quotes": ranked_quotes,
			},
			"errors": errors,
			"recommendations": recommendations,
		}

		if ranked_quotes:
			status = "Quotes Received"
		elif errors:
			status = "API Error"
		else:
			status = "No Quotes Received"

	except frappe.ValidationError as e:
		frappe.local.response["http_status_code"] = 400
		status = "API Error"
		response_payload = {"status": "error", "engine": FLOWWOLF_ENGINE, "message": str(e)}
	except Exception as e:
		frappe.log_error(message=frappe.get_traceback(), title="FlowWolf get_rates API Error")
		status = "Connection Failed" if "timeout" in str(e).lower() else "API Error"
		response_payload = {"status": "error", "engine": FLOWWOLF_ENGINE, "message": str(e)}
	finally:
		log_body = {**(body or {}), "api_url": FLOWWOLF_API_ENDPOINT}
		log_carrier_id = carrier_id or "Multi-Carrier"
		log_api_transaction(headers, log_body, response_payload, status, log_carrier_id)

	return response_payload


@frappe.whitelist(allow_guest=False)
def get_service_eligibility(payload=None, origin=None, destination=None, date=None, **kwargs):
	"""FlowWolf gateway for Dayton GET /api/Shipping/ServiceEligibility."""
	headers, body = _read_request_context()
	status = "Queued"
	response_payload: dict = {}
	origin_zip = destination_zip = shipment_date = None

	try:
		if isinstance(payload, str):
			payload = frappe.parse_json(payload)
		payload = payload or body or {}
		origin_zip = (
			origin
			or payload.get("origin")
			or payload.get("origin_zip")
			or kwargs.get("origin")
		)
		destination_zip = (
			destination
			or payload.get("destination")
			or payload.get("destination_zip")
			or kwargs.get("destination")
		)
		shipment_date = date or payload.get("date") or payload.get("shipment_date") or kwargs.get("date")

		if not origin_zip or not destination_zip:
			frappe.throw("origin and destination ZIP codes are required.")

		from ltl_quote.carrier_network.adapters.dayton import DaytonCarrierAdapter

		adapter = DaytonCarrierAdapter()
		result = adapter.get_service_eligibility(origin_zip, destination_zip, shipment_date)
		if not result:
			status = "API Error"
			response_payload = {
				"status": "error",
				"engine": FLOWWOLF_ENGINE,
				"message": "Service eligibility lookup failed or returned no data.",
			}
		else:
			status = "Success"
			response_payload = {
				"status": "success",
				"engine": FLOWWOLF_ENGINE,
				"data": result,
				**result,
			}
	except frappe.ValidationError as e:
		frappe.local.response["http_status_code"] = 400
		status = "API Error"
		response_payload = {"status": "error", "engine": FLOWWOLF_ENGINE, "message": str(e)}
	except Exception as e:
		frappe.log_error(message=frappe.get_traceback(), title="FlowWolf get_service_eligibility Error")
		status = "Connection Failed" if "timeout" in str(e).lower() else "API Error"
		response_payload = {"status": "error", "engine": FLOWWOLF_ENGINE, "message": str(e)}
	finally:
		log_body = {
			**(body or {}),
			"origin": origin_zip,
			"destination": destination_zip,
			"date": shipment_date,
		}
		log_api_transaction(headers, log_body, response_payload, status, "DAYTON")

	return response_payload


@frappe.whitelist(allow_guest=False)
def search_dayton_images(payload=None, pro=None, **kwargs):
	"""FlowWolf gateway for Dayton GET /api/Images/Search."""
	headers, body = _read_request_context()
	status = "Queued"
	response_payload: dict = {}
	pro_number = None

	try:
		if isinstance(payload, str):
			payload = frappe.parse_json(payload)
		payload = payload or body or {}
		pro_number = pro or payload.get("pro") or payload.get("pro_number") or kwargs.get("pro")
		if not pro_number:
			frappe.throw("pro is required.")

		from ltl_quote.api.shipping import search_dayton_images as _search_dayton_images

		result = _search_dayton_images(pro=pro_number)
		status = "Success" if result.get("status") == "success" else "API Error"
		response_payload = {"engine": FLOWWOLF_ENGINE, **result}
	except frappe.ValidationError as e:
		frappe.local.response["http_status_code"] = 400
		status = "API Error"
		response_payload = {"status": "error", "engine": FLOWWOLF_ENGINE, "message": str(e)}
	except Exception as e:
		frappe.log_error(message=frappe.get_traceback(), title="FlowWolf search_dayton_images Error")
		status = "Connection Failed" if "timeout" in str(e).lower() else "API Error"
		response_payload = {"status": "error", "engine": FLOWWOLF_ENGINE, "message": str(e)}
	finally:
		log_body = {**(body or {}), "pro": pro_number}
		log_api_transaction(headers, log_body, response_payload, status, "DAYTON")

	return response_payload


@frappe.whitelist(allow_guest=False)
def dayton_document_available(payload=None, pro=None, doc_type="BILL OF LADING", **kwargs):
	"""FlowWolf gateway for Dayton document index verification."""
	headers, body = _read_request_context()
	status = "Queued"
	response_payload: dict = {}
	pro_number = None

	try:
		if isinstance(payload, str):
			payload = frappe.parse_json(payload)
		payload = payload or body or {}
		pro_number = pro or payload.get("pro") or payload.get("pro_number") or kwargs.get("pro")
		doc_type = (
			doc_type
			or payload.get("doc_type")
			or payload.get("document_type")
			or kwargs.get("doc_type")
			or "BILL OF LADING"
		)
		if not pro_number:
			frappe.throw("pro is required.")

		from ltl_quote.api.shipping import dayton_document_available as _dayton_document_available

		result = _dayton_document_available(pro=pro_number, doc_type=doc_type)
		status = "Success"
		response_payload = {"status": "success", "engine": FLOWWOLF_ENGINE, **result}
	except frappe.ValidationError as e:
		frappe.local.response["http_status_code"] = 400
		status = "API Error"
		response_payload = {"status": "error", "engine": FLOWWOLF_ENGINE, "message": str(e)}
	except Exception as e:
		frappe.log_error(message=frappe.get_traceback(), title="FlowWolf dayton_document_available Error")
		status = "Connection Failed" if "timeout" in str(e).lower() else "API Error"
		response_payload = {"status": "error", "engine": FLOWWOLF_ENGINE, "message": str(e)}
	finally:
		log_body = {**(body or {}), "pro": pro_number, "doc_type": doc_type}
		log_api_transaction(headers, log_body, response_payload, status, "DAYTON")

	return response_payload


@frappe.whitelist(allow_guest=False)
def refresh_dayton_shipment_bol(payload=None, shipment=None, shipment_name=None, **kwargs):
	"""FlowWolf gateway to fetch a Dayton BOL when Images/Search reports it indexed."""
	headers, body = _read_request_context()
	status = "Queued"
	response_payload: dict = {}
	shipment_id = None

	try:
		if isinstance(payload, str):
			payload = frappe.parse_json(payload)
		payload = payload or body or {}
		shipment_id = (
			shipment
			or shipment_name
			or payload.get("shipment")
			or payload.get("shipment_name")
			or payload.get("shipment_id")
			or kwargs.get("shipment")
		)
		if not shipment_id:
			frappe.throw("shipment is required.")

		from ltl_quote.api.shipping import refresh_dayton_shipment_bol as _refresh_dayton_shipment_bol

		result = _refresh_dayton_shipment_bol(shipment=shipment_id)
		status = "Success" if result.get("status") == "success" else "API Error"
		response_payload = {"engine": FLOWWOLF_ENGINE, **result}
	except frappe.ValidationError as e:
		frappe.local.response["http_status_code"] = 400
		status = "API Error"
		response_payload = {"status": "error", "engine": FLOWWOLF_ENGINE, "message": str(e)}
	except Exception as e:
		frappe.log_error(message=frappe.get_traceback(), title="FlowWolf refresh_dayton_shipment_bol Error")
		status = "Connection Failed" if "timeout" in str(e).lower() else "API Error"
		response_payload = {"status": "error", "engine": FLOWWOLF_ENGINE, "message": str(e)}
	finally:
		log_body = {**(body or {}), "shipment": shipment_id}
		log_api_transaction(headers, log_body, response_payload, status, "DAYTON")

	return response_payload


def _attach_dayton_indexed_documents(payload: dict, carrier_code: str | None, pro_number: str | None) -> None:
	"""Add Images/Search summary to shipment detail payloads for Dayton carriers."""
	if str(carrier_code or "").upper() != "DAYTON":
		return
	pro = str(pro_number or "").strip()
	if not pro:
		return

	from ltl_quote.carrier_network.adapters.dayton import get_dayton_indexed_documents

	indexed = get_dayton_indexed_documents(pro)
	payload["indexed_documents"] = indexed
	payload["bol_indexed"] = bool(indexed.get("bol_available"))


def _attach_dayton_pickup(payload: dict, shipment_name: str | None, carrier_code: str | None) -> None:
	"""Add pickup summary to shipment detail payloads for Dayton carriers."""
	if str(carrier_code or "").upper() != "DAYTON" or not shipment_name:
		return

	from ltl_quote.carrier_network.adapters.dayton import DaytonCarrierAdapter
	from ltl_quote.carrier_network.pickup import PICKUP_TERMINAL_STATUSES, shipment_pickup_summary

	doc = frappe.get_doc("LTL Shipment", shipment_name)
	live = bool(doc.pickup_number) and str(doc.pickup_status or "") not in PICKUP_TERMINAL_STATUSES
	adapter = DaytonCarrierAdapter() if live else None
	payload["pickup"] = shipment_pickup_summary(doc, live=live, adapter=adapter)


def _flowwolf_pickup_proxy(headers, body, request_kwargs, handler, log_fields=()):
	status = "Queued"
	response_payload: dict = {}
	log_values = {}

	try:
		payload = request_kwargs.get("payload")
		if isinstance(payload, str):
			payload = frappe.parse_json(payload)
		request = {**(body or {}), **(payload or {}), **request_kwargs}
		for field in log_fields:
			log_values[field] = request.get(field)
		response_payload = handler(request)
		status = "Success" if response_payload.get("status") == "success" else "API Error"
		response_payload = {"engine": FLOWWOLF_ENGINE, **response_payload}
	except frappe.ValidationError as e:
		frappe.local.response["http_status_code"] = 400
		status = "API Error"
		response_payload = {"status": "error", "engine": FLOWWOLF_ENGINE, "message": str(e)}
	except Exception as e:
		frappe.log_error(message=frappe.get_traceback(), title="FlowWolf Dayton Pickup Error")
		status = "Connection Failed" if "timeout" in str(e).lower() else "API Error"
		response_payload = {"status": "error", "engine": FLOWWOLF_ENGINE, "message": str(e)}
	finally:
		log_body = {**(body or {}), **log_values}
		log_api_transaction(headers, log_body, response_payload, status, "DAYTON")

	return response_payload


@frappe.whitelist(allow_guest=False)
def create_dayton_pickup(payload=None, shipment=None, shipment_name=None, **kwargs):
	"""FlowWolf gateway for Dayton PUT /api/Pickup."""
	headers, body = _read_request_context()

	def handler(request):
		from ltl_quote.api.shipping import create_dayton_pickup as _create_dayton_pickup

		return _create_dayton_pickup(
			shipment=request.get("shipment") or shipment,
			shipment_name=request.get("shipment_name") or shipment_name,
		)

	return _flowwolf_pickup_proxy(
		headers,
		body,
		{"payload": payload, "shipment": shipment, "shipment_name": shipment_name, **kwargs},
		handler,
		("shipment", "shipment_name"),
	)


@frappe.whitelist(allow_guest=False)
def get_dayton_pickup(payload=None, shipment=None, shipment_name=None, number=None, **kwargs):
	"""FlowWolf gateway for Dayton GET /api/Pickup."""
	headers, body = _read_request_context()

	def handler(request):
		from ltl_quote.api.shipping import get_dayton_pickup as _get_dayton_pickup

		return _get_dayton_pickup(
			shipment=request.get("shipment") or shipment,
			shipment_name=request.get("shipment_name") or shipment_name,
			number=request.get("number") or number,
		)

	return _flowwolf_pickup_proxy(
		headers,
		body,
		{"payload": payload, "shipment": shipment, "shipment_name": shipment_name, "number": number, **kwargs},
		handler,
		("shipment", "number"),
	)


@frappe.whitelist(allow_guest=False)
def update_dayton_pickup(payload=None, shipment=None, shipment_name=None, **kwargs):
	"""FlowWolf gateway for Dayton POST /api/Pickup."""
	headers, body = _read_request_context()

	def handler(request):
		from ltl_quote.api.shipping import update_dayton_pickup as _update_dayton_pickup

		return _update_dayton_pickup(
			shipment=request.get("shipment") or shipment,
			shipment_name=request.get("shipment_name") or shipment_name,
			payload=request.get("payload") or payload,
		)

	return _flowwolf_pickup_proxy(
		headers,
		body,
		{"payload": payload, "shipment": shipment, "shipment_name": shipment_name, **kwargs},
		handler,
		("shipment",),
	)


@frappe.whitelist(allow_guest=False)
def update_dayton_pickup_by_psid(payload=None, shipment=None, shipment_name=None, psid=None, **kwargs):
	"""FlowWolf gateway for Dayton POST /api/Pickup/ByPSID."""
	headers, body = _read_request_context()

	def handler(request):
		from ltl_quote.api.shipping import update_dayton_pickup_by_psid as _update_dayton_pickup_by_psid

		return _update_dayton_pickup_by_psid(
			shipment=request.get("shipment") or shipment,
			shipment_name=request.get("shipment_name") or shipment_name,
			payload=request.get("payload") or payload,
			psid=request.get("psid") or psid,
		)

	return _flowwolf_pickup_proxy(
		headers,
		body,
		{"payload": payload, "shipment": shipment, "shipment_name": shipment_name, "psid": psid, **kwargs},
		handler,
		("shipment", "psid"),
	)


@frappe.whitelist(allow_guest=False)
def cancel_dayton_pickup(payload=None, shipment=None, shipment_name=None, number=None, **kwargs):
	"""FlowWolf gateway for Dayton DELETE /api/Pickup/Cancel."""
	headers, body = _read_request_context()

	def handler(request):
		from ltl_quote.api.shipping import cancel_dayton_pickup as _cancel_dayton_pickup

		return _cancel_dayton_pickup(
			shipment=request.get("shipment") or shipment,
			shipment_name=request.get("shipment_name") or shipment_name,
			number=request.get("number") or number,
		)

	return _flowwolf_pickup_proxy(
		headers,
		body,
		{"payload": payload, "shipment": shipment, "shipment_name": shipment_name, "number": number, **kwargs},
		handler,
		("shipment", "number"),
	)


@frappe.whitelist(allow_guest=False)
def book_carrier_quote(
	quote_request_id: str,
	carrier_code: str | None = None,
	quote_row_idx: int | None = None,
	transit_days: int | None = None,
	is_test: bool = False,
	carrier_quote_id: str | None = None,
):
	"""Server-side gateway to book an engineered quote from the Frappe desk UI."""
	result = _book_quote_core(
		quote_request_id=quote_request_id,
		carrier_code=carrier_code,
		quote_row_idx=quote_row_idx,
		carrier_quote_id=carrier_quote_id,
		transit_days=transit_days,
		is_test=is_test,
	)
	return {
		"status": "success",
		"message": (
			f"Successfully booked with {result['carrier_name']}! "
			f"BOL Generated: {result.get('bol_number')}"
		),
		"data": result,
	}


@frappe.whitelist(allow_guest=False)
def create_bol(payload=None, **kwargs):
	"""
	FlowWolf unified multi-carrier BOL gateway.

	POST /api/method/ltl_quote.api.flowwolf.create_bol

	Request body (JSON):
	    {
	        "quote_request_id": "LTL-QR-2026-00141",
	        "is_test": false,
	        "items": [
	            {
	                "description": "Quiet Qurl Perimeter Isolation 2\\"",
	                "freight_class": "300",
	                "nmfc": "103300-2",
	                "quantity": 1,
	                "weight": 1200
	            }
	        ]
	    }

	Optional overrides:
	    - carrier_preference / carrier_code — book that carrier only
	    - carrier_quote_id — book the matching rate line
	    - quote_row_idx — book by child-table index
	    - items — upsert quote-request line items before booking (BOL commodities)

	When none of the carrier selectors are provided (or carrier_quote_id is a
	placeholder), defaults to the cheapest quote on the request (or
	selected_carrier_quote if set).

	If the quote is already booked, returns status ``already_booked`` with the
	existing shipment / BOL fields (does not create a second BOL).
	"""
	headers, body = _read_request_context()
	status = "Queued"
	response_payload: dict = {}
	carrier_id = None
	request = {}

	try:
		raw = payload if isinstance(payload, dict) else {}
		if not raw:
			raw = body or {}
		request = {**raw, **{k: v for k, v in kwargs.items() if v is not None}}
		for key in ("quote_request_id", "carrier_preference", "carrier_code", "carrier_quote_id", "items"):
			if body.get(key) is not None and not request.get(key):
				request[key] = body[key]

		quote_request_id = request.get("quote_request_id")
		if not quote_request_id:
			frappe.throw("quote_request_id is required to create a BOL.")

		carrier_preference = (
			request.get("carrier_preference") or request.get("carrier_code") or ""
		)
		carrier_id = resolve_carrier_id(carrier_preference) if carrier_preference else None

		quote_row_idx = request.get("quote_row_idx")
		if quote_row_idx is not None and quote_row_idx != "":
			quote_row_idx = int(quote_row_idx)
		else:
			quote_row_idx = None

		is_test = request.get("is_test")
		if isinstance(is_test, str):
			is_test = is_test.strip().lower() in ("1", "true", "yes", "y")
		else:
			is_test = bool(is_test)

		booking = _book_quote_core(
			quote_request_id=str(quote_request_id),
			carrier_code=carrier_preference or None,
			quote_row_idx=quote_row_idx,
			carrier_quote_id=request.get("carrier_quote_id"),
			transit_days=request.get("transit_days"),
			is_test=is_test,
			items=request.get("items"),
		)
		carrier_id = booking.get("carrier_code") or carrier_id

		booking_status = booking.get("status") or "success"
		response_payload = {
			"status": booking_status,
			"engine": FLOWWOLF_ENGINE,
			"quote_request_id": quote_request_id,
			"message": booking.get("message") or "",
			"data": {
				"shipment": booking.get("shipment"),
				"carrier_code": booking.get("carrier_code"),
				"carrier_name": booking.get("carrier_name"),
				"bol_number": booking.get("bol_number"),
				"pro_number": booking.get("pro_number"),
				"bol_document_url": booking.get("bol_document_url") or "",
				"total_charge": booking.get("total_charge"),
			},
		}
		status = "Already Booked" if booking_status == "already_booked" else "Booked"

	except frappe.ValidationError as e:
		frappe.local.response["http_status_code"] = 400
		status = "API Error"
		response_payload = {"status": "error", "engine": FLOWWOLF_ENGINE, "message": str(e)}
	except Exception as e:
		frappe.log_error(message=frappe.get_traceback(), title="FlowWolf create_bol API Error")
		status = "Connection Failed" if "timeout" in str(e).lower() else "API Error"
		response_payload = {"status": "error", "engine": FLOWWOLF_ENGINE, "message": str(e)}
	finally:
		log_body = {**(request or body or {}), "api_url": FLOWWOLF_BOL_ENDPOINT}
		log_carrier_id = carrier_id or "Multi-Carrier"
		log_api_transaction(headers, log_body, response_payload, status, log_carrier_id)

	return response_payload


def _book_quote_core(
	quote_request_id: str,
	carrier_code: str | None = None,
	quote_row_idx: int | None = None,
	carrier_quote_id: str | None = None,
	transit_days: int | None = None,
	is_test: bool = False,
	items=None,
) -> dict:
	"""Shared booking path for desk book_carrier_quote and FlowWolf create_bol."""
	if not frappe.db.exists("LTL Quote Request", quote_request_id):
		frappe.throw(f"Quote Request record {quote_request_id} not found.")

	quote_doc = frappe.get_doc("LTL Quote Request", quote_request_id)
	existing_shipment = frappe.db.get_value("LTL Shipment", {"quote_request": quote_request_id}, "name")

	if quote_doc.status == "Booked" or existing_shipment:
		return _already_booked_result(quote_doc, existing_shipment)

	# Optional Postman/UI line items → persist before Dayton eBOL build.
	if items not in (None, "", []):
		_upsert_quote_request_line_items(quote_doc, items)
		quote_doc.save(ignore_permissions=True)
		quote_doc.reload()

	if not quote_doc.carrier_quotes:
		frappe.throw("No carrier quotes are available on this request. Fetch rates before booking.")

	row_idx = _resolve_quote_row_index(
		quote_doc,
		carrier_code=carrier_code,
		quote_row_idx=quote_row_idx,
		carrier_quote_id=carrier_quote_id,
	)
	selected = quote_doc.carrier_quotes[row_idx]
	carrier_id = resolve_carrier_id(carrier_code) if carrier_code else selected.carrier
	if not carrier_id:
		carrier_id = selected.carrier

	carrier_docs, carrier_label = load_carrier_for_rating(carrier_id)
	carrier_doc = carrier_docs[0]
	if carrier_doc.connector_type == "Mock" and carrier_code and str(carrier_code).upper() != "MOCK":
		frappe.throw(f"Booking automation for {carrier_code} is not implemented yet.")

	enrich_location_fields(quote_doc, "origin")
	enrich_location_fields(quote_doc, "destination")
	origin_city, origin_state = resolve_us_location(
		quote_doc.origin_zip,
		quote_doc.origin_city,
		quote_doc.origin_state,
	)
	if not origin_state:
		frappe.throw(
			"Origin state is required to book a shipment. Enter origin state or use a valid US origin ZIP."
		)

	if transit_days:
		selected.transit_days = int(transit_days)

	quote_doc.selected_carrier_quote = str(row_idx)
	quote_doc.origin_city = origin_city or quote_doc.origin_city
	quote_doc.origin_state = origin_state
	quote_doc.final_carrier = carrier_id
	quote_doc.final_charge = flt(selected.total_charge)
	quote_doc.carrier_reference_number = str(selected.carrier_quote_id or "")

	try:
		result = ShipmentExecutor(quote_doc).book(is_test=is_test)
	except Exception:
		frappe.log_error(message=frappe.get_traceback(), title="FlowWolf Booking Error")
		raise

	carrier_name = carrier_doc.carrier_name or carrier_label
	bol_url = resolve_shipment_bol_url(
		shipment_name=result.get("shipment"),
		quote_request=quote_doc,
	) or result.get("bol_document_url") or ""

	return {
		**result,
		"status": "success",
		"carrier_code": carrier_id,
		"carrier_name": carrier_name,
		"origin_city": origin_city,
		"origin_state": origin_state,
		"bol_document_url": bol_url,
		"total_charge": flt(selected.total_charge),
	}


def _already_booked_result(quote_doc, shipment_name: str | None = None) -> dict:
	"""Return existing shipment/BOL details instead of throwing on re-book."""
	shipment_name = shipment_name or frappe.db.get_value(
		"LTL Shipment", {"quote_request": quote_doc.name}, "name"
	)
	shipment = frappe.get_doc("LTL Shipment", shipment_name) if shipment_name else None

	bol_number = (
		(shipment.bol_number if shipment else None)
		or getattr(quote_doc, "bol_number", None)
		or ""
	)
	pro_number = (
		(shipment.pro_number if shipment else None)
		or getattr(quote_doc, "pro_number", None)
		or ""
	)
	bol_url = resolve_shipment_bol_url(
		shipment_name=shipment_name,
		quote_request=quote_doc,
	) or (getattr(shipment, "bol_document_url", None) if shipment else "") or getattr(
		quote_doc, "bol_document_url", None
	) or ""

	carrier_code = (
		(shipment.carrier if shipment else None)
		or getattr(quote_doc, "final_carrier", None)
		or ""
	)
	carrier_name = ""
	if shipment and getattr(shipment, "carrier_name", None):
		carrier_name = shipment.carrier_name
	elif carrier_code and frappe.db.exists("LTL Carrier", carrier_code):
		carrier_name = frappe.db.get_value("LTL Carrier", carrier_code, "carrier_name") or ""

	total_charge = (
		flt(shipment.total_charge) if shipment and shipment.total_charge is not None else flt(quote_doc.final_charge)
	)

	return {
		"status": "already_booked",
		"message": f"Quote Request {quote_doc.name} is already booked.",
		"shipment": shipment_name,
		"bol_number": bol_number,
		"pro_number": pro_number,
		"bol_document_url": bol_url,
		"dayton_bol_id": getattr(shipment, "dayton_bol_id", None) if shipment else None,
		"carrier_code": carrier_code,
		"carrier_name": carrier_name,
		"total_charge": total_charge,
	}


@frappe.whitelist(allow_guest=False)
def get_shipment_details(shipment=None, quote_request_id=None, payload=None, **kwargs):
	"""
	Retrieve booked shipment / BOL / carrier details from stored LTL Shipment data.

	GET or POST /api/method/ltl_quote.api.flowwolf.get_shipment_details

	Provide either:
	    { "shipment": "LTL-SHP-2026-00095" }
	or:
	    { "quote_request_id": "LTL-QR-2026-00201" }

	Carrier is resolved from the shipment record — no ArcBest/Dayton parameter needed.
	"""
	headers, body = _read_request_context()
	request = {}
	if isinstance(payload, str) and payload.strip():
		try:
			request = json.loads(payload)
		except Exception:
			request = {}
	elif isinstance(payload, dict):
		request = payload
	request = {**(body or {}), **request, **(kwargs or {})}

	shipment_id = (
		shipment
		or request.get("shipment")
		or request.get("shipment_id")
		or request.get("shipment_name")
	)
	quote_id = (
		quote_request_id
		or request.get("quote_request_id")
		or request.get("quote_request")
	)
	shipment_id = str(shipment_id or "").strip() or None
	quote_id = str(quote_id or "").strip() or None

	status = "Queued"
	response_payload: dict = {}
	carrier_id = None

	try:
		if not shipment_id and not quote_id:
			frappe.throw("Please provide either a shipment ID or a quote_request_id.")

		filters = {}
		if shipment_id:
			filters["name"] = shipment_id
		elif quote_id:
			filters["quote_request"] = quote_id

		shipment_doc = frappe.db.get_value(
			"LTL Shipment",
			filters,
			[
				"name",
				"quote_request",
				"carrier",
				"carrier_name",
				"bol_number",
				"pro_number",
				"dayton_bol_id",
				"bol_document",
				"bol_document_url",
				"total_charge",
				"status",
			],
			as_dict=True,
		)

		if not shipment_doc and quote_id and not shipment_id:
			# Fall back to quote-request fields when shipment row is missing.
			quote_doc = frappe.db.get_value(
				"LTL Quote Request",
				quote_id,
				[
					"name",
					"status",
					"final_carrier",
					"bol_number",
					"pro_number",
					"bol_document_url",
					"final_charge",
				],
				as_dict=True,
			)
			if not quote_doc:
				frappe.throw("No shipment found matching the provided identifiers.")
			carrier_id = quote_doc.final_carrier or None
			carrier_name = ""
			if carrier_id and frappe.db.exists("LTL Carrier", carrier_id):
				carrier_name = frappe.db.get_value("LTL Carrier", carrier_id, "carrier_name") or ""
			bol_url = resolve_shipment_bol_url(quote_request=quote_doc) or quote_doc.bol_document_url or ""
			response_payload = {
				"status": "success",
				"engine": FLOWWOLF_ENGINE,
				"shipment": None,
				"quote_request_id": quote_doc.name,
				"carrier_code": carrier_id or "",
				"carrier_name": carrier_name,
				"bol_number": quote_doc.bol_number or "",
				"pro_number": quote_doc.pro_number or "",
				"bol_document_url": bol_url,
				"total_charge": flt(quote_doc.final_charge),
				"shipment_status": quote_doc.status or "",
			}
			_attach_dayton_indexed_documents(response_payload, carrier_id, quote_doc.pro_number)
			status = "Quotes Received"
		elif not shipment_doc:
			frappe.throw("No shipment found matching the provided identifiers.")
		else:
			carrier_id = shipment_doc.carrier or None
			bol_url = resolve_shipment_bol_url(
				shipment_name=shipment_doc.name,
				quote_request=shipment_doc.quote_request,
			) or shipment_doc.bol_document_url or ""

			response_payload = {
				"status": "success",
				"engine": FLOWWOLF_ENGINE,
				"shipment": shipment_doc.name,
				"quote_request_id": shipment_doc.quote_request or quote_id or "",
				"carrier_code": shipment_doc.carrier or "",
				"carrier_name": shipment_doc.carrier_name or "",
				"bol_number": shipment_doc.bol_number or "",
				"pro_number": shipment_doc.pro_number or "",
				"dayton_bol_id": shipment_doc.dayton_bol_id or "",
				"bol_document_url": bol_url,
				"total_charge": flt(shipment_doc.total_charge),
				"shipment_status": shipment_doc.status or "",
			}
			_attach_dayton_indexed_documents(
				response_payload,
				shipment_doc.carrier,
				shipment_doc.pro_number,
			)
			_attach_dayton_pickup(response_payload, shipment_doc.name, shipment_doc.carrier)
			status = "Booked" if (shipment_doc.status or "").lower() in {"booked", "dispatched", "in transit", "delivered"} else "Quotes Received"

	except frappe.ValidationError as e:
		frappe.local.response["http_status_code"] = 400
		status = "API Error"
		response_payload = {"status": "error", "engine": FLOWWOLF_ENGINE, "message": str(e)}
	except Exception as e:
		frappe.log_error(message=frappe.get_traceback(), title="FlowWolf get_shipment_details API Error")
		status = "API Error"
		response_payload = {"status": "error", "engine": FLOWWOLF_ENGINE, "message": str(e)}
	finally:
		log_body = {
			**(body or {}),
			"api_url": FLOWWOLF_SHIPMENT_DETAILS_ENDPOINT,
			"shipment": shipment_id,
			"quote_request_id": quote_id,
		}
		log_api_transaction(headers, log_body, response_payload, status, carrier_id or "Multi-Carrier")

	return response_payload


def _merge_flowwolf_request(payload=None, **kwargs) -> dict:
	"""Merge JSON body, form dict, payload arg, and kwargs into one request map."""
	_headers, body = _read_request_context()
	request: dict = {}
	if isinstance(payload, str) and payload.strip():
		try:
			request = json.loads(payload)
		except Exception:
			request = {}
	elif isinstance(payload, dict):
		request = payload
	return {**(body or {}), **request, **(kwargs or {})}


def _resolve_shipment_for_tracking(
	shipment: str | None = None,
	quote_request_id: str | None = None,
	pro_number: str | None = None,
) -> tuple[str | None, str | None]:
	"""Return (shipment_name, pro_number) from any supported identifier."""
	shipment_id = str(shipment or "").strip() or None
	quote_id = str(quote_request_id or "").strip() or None
	pro = str(pro_number or "").strip() or None

	row = None
	if shipment_id:
		row = frappe.db.get_value(
			"LTL Shipment",
			shipment_id,
			["name", "pro_number"],
			as_dict=True,
		)
	elif quote_id:
		row = frappe.db.get_value(
			"LTL Shipment",
			{"quote_request": quote_id},
			["name", "pro_number"],
			as_dict=True,
		)
	elif pro:
		row = frappe.db.get_value(
			"LTL Shipment",
			{"pro_number": pro},
			["name", "pro_number"],
			as_dict=True,
		)

	if row:
		return row.name, str(row.pro_number or "").strip() or pro
	return None, pro


def _serialize_tracking_events(shipment_doc) -> list[dict]:
	events = []
	for row in shipment_doc.get("tracking_events") or []:
		events.append(
			{
				"event_datetime": row.event_datetime,
				"status_code": row.status_code,
				"status_description": row.status_description,
				"location": row.location,
				"is_exception": int(row.is_exception or 0),
				"exception_type": row.exception_type,
				"source": row.source,
			}
		)
	return events


def _enrich_dayton_results_with_local_shipments(results: list) -> list:
	"""Attach matching local LTL Shipment ids to Dayton result rows when PRO matches."""
	enriched = []
	for item in results or []:
		row = dict(item) if isinstance(item, dict) else {"raw": item}
		pro = str(
			row.get("pro")
			or row.get("proNumber")
			or row.get("pro_number")
			or row.get("number")
			or ""
		).strip()
		if pro:
			shipment_name = frappe.db.get_value("LTL Shipment", {"pro_number": pro}, "name")
			if shipment_name:
				row["local_shipment"] = shipment_name
				local = frappe.db.get_value(
					"LTL Shipment",
					shipment_name,
					["status", "current_status", "quote_request"],
					as_dict=True,
				)
				if local:
					row["local_shipment_status"] = local.status
					row["local_current_status"] = local.current_status
					row["quote_request_id"] = local.quote_request
		enriched.append(row)
	return enriched


@frappe.whitelist(allow_guest=False)
def track_by_number(payload=None, **kwargs):
	"""
	Live Dayton Track-by-Number (PRO) and persist events when a local shipment matches.

	POST /api/method/ltl_quote.api.flowwolf.track_by_number

	Body examples:
	    { "pro_number": "09019812894" }
	    { "shipment": "LTL-SHP-2026-00095" }
	    { "quote_request_id": "LTL-QR-2026-00201" }
	"""
	headers, body = _read_request_context()
	request = _merge_flowwolf_request(payload, **kwargs)
	status = "Queued"
	response_payload: dict = {}
	carrier_id = "DAYTON"

	shipment_id = (
		request.get("shipment")
		or request.get("shipment_id")
		or request.get("shipment_name")
	)
	quote_id = request.get("quote_request_id") or request.get("quote_request")
	pro_number = (
		request.get("pro_number")
		or request.get("pro")
		or request.get("number")
		or request.get("tracking_number")
	)

	try:
		shipment_name, pro = _resolve_shipment_for_tracking(shipment_id, quote_id, pro_number)
		if not pro and not shipment_name:
			frappe.throw("Provide pro_number, shipment, or quote_request_id.")

		if shipment_name:
			shipment = frappe.get_doc("LTL Shipment", shipment_name)
			if not pro:
				pro = str(shipment.pro_number or "").strip()
			if not pro:
				frappe.throw(f"Shipment {shipment_name} has no PRO / tracking number yet.")
			carrier_id = shipment.carrier or "DAYTON"
			from ltl_quote.visibility.tracker import ShipmentTracker

			result = ShipmentTracker(shipment).refresh()
			shipment.reload()
			events = result.get("events") or _serialize_tracking_events(shipment)
			response_payload = {
				"status": "success",
				"engine": FLOWWOLF_ENGINE,
				"shipment": shipment.name,
				"quote_request_id": shipment.quote_request,
				"pro_number": pro,
				"carrier_code": shipment.carrier,
				"shipment_status": shipment.status,
				"current_status": shipment.current_status,
				"current_location": shipment.current_location,
				"has_exception": bool(result.get("has_exception") or shipment.has_exception),
				"events": events,
				"message": (
					"Tracking details synchronized successfully."
					if events
					else "Shipment registered, but transit tracking events are not populated yet."
				),
			}
		else:
			from ltl_quote.carrier_network.adapters.dayton import DaytonCarrierAdapter

			adapter = DaytonCarrierAdapter()
			events = adapter.get_tracking(pro)
			response_payload = {
				"status": "success",
				"engine": FLOWWOLF_ENGINE,
				"shipment": None,
				"pro_number": pro,
				"carrier_code": "DAYTON",
				"events": events,
				"message": (
					"Live tracking events retrieved (no local shipment matched)."
					if events
					else "No tracking events returned for this PRO yet."
				),
			}
		status = "Quotes Received" if response_payload.get("events") else "No Quotes Received"

	except frappe.ValidationError as e:
		frappe.local.response["http_status_code"] = 400
		status = "API Error"
		response_payload = {"status": "error", "engine": FLOWWOLF_ENGINE, "message": str(e)}
	except Exception as e:
		frappe.log_error(message=frappe.get_traceback(), title="FlowWolf track_by_number API Error")
		status = "API Error"
		response_payload = {"status": "error", "engine": FLOWWOLF_ENGINE, "message": str(e)}
	finally:
		log_body = {**(body or {}), "api_url": FLOWWOLF_TRACK_BY_NUMBER_ENDPOINT, **request}
		log_api_transaction(headers, log_body, response_payload, status, carrier_id)

	return response_payload


@frappe.whitelist(allow_guest=False)
def track_history(payload=None, **kwargs):
	"""
	Return tracking history events for a shipment/PRO.

	Uses Dayton ByNumber under the hood (no separate History URL). Refreshes then
	returns persisted LTL Shipment.tracking_events when a local shipment exists.

	POST /api/method/ltl_quote.api.flowwolf.track_history
	"""
	headers, body = _read_request_context()
	request = _merge_flowwolf_request(payload, **kwargs)
	status = "Queued"
	response_payload: dict = {}
	carrier_id = "DAYTON"
	refresh = str(request.get("refresh", "1")).strip().lower() not in {"0", "false", "no"}

	shipment_id = (
		request.get("shipment")
		or request.get("shipment_id")
		or request.get("shipment_name")
	)
	quote_id = request.get("quote_request_id") or request.get("quote_request")
	pro_number = (
		request.get("pro_number")
		or request.get("pro")
		or request.get("number")
		or request.get("tracking_number")
	)

	try:
		shipment_name, pro = _resolve_shipment_for_tracking(shipment_id, quote_id, pro_number)
		if not pro and not shipment_name:
			frappe.throw("Provide pro_number, shipment, or quote_request_id.")

		if shipment_name:
			shipment = frappe.get_doc("LTL Shipment", shipment_name)
			pro = pro or str(shipment.pro_number or "").strip()
			if not pro:
				frappe.throw(f"Shipment {shipment_name} has no PRO / tracking number yet.")
			carrier_id = shipment.carrier or "DAYTON"
			if refresh:
				from ltl_quote.visibility.tracker import ShipmentTracker

				ShipmentTracker(shipment).refresh()
				shipment.reload()
			events = _serialize_tracking_events(shipment)
			response_payload = {
				"status": "success",
				"engine": FLOWWOLF_ENGINE,
				"shipment": shipment.name,
				"quote_request_id": shipment.quote_request,
				"pro_number": pro,
				"carrier_code": shipment.carrier,
				"shipment_status": shipment.status,
				"current_status": shipment.current_status,
				"current_location": shipment.current_location,
				"last_tracking_update": shipment.last_tracking_update,
				"events": events,
			}
		else:
			from ltl_quote.carrier_network.adapters.dayton import DaytonCarrierAdapter

			adapter = DaytonCarrierAdapter()
			events = adapter.get_tracking(pro)
			response_payload = {
				"status": "success",
				"engine": FLOWWOLF_ENGINE,
				"shipment": None,
				"pro_number": pro,
				"carrier_code": "DAYTON",
				"events": events,
			}
		status = "Quotes Received" if response_payload.get("events") else "No Quotes Received"

	except frappe.ValidationError as e:
		frappe.local.response["http_status_code"] = 400
		status = "API Error"
		response_payload = {"status": "error", "engine": FLOWWOLF_ENGINE, "message": str(e)}
	except Exception as e:
		frappe.log_error(message=frappe.get_traceback(), title="FlowWolf track_history API Error")
		status = "API Error"
		response_payload = {"status": "error", "engine": FLOWWOLF_ENGINE, "message": str(e)}
	finally:
		log_body = {**(body or {}), "api_url": FLOWWOLF_TRACK_HISTORY_ENDPOINT, **request}
		log_api_transaction(headers, log_body, response_payload, status, carrier_id)

	return response_payload


@frappe.whitelist(allow_guest=False)
def track_by_date(payload=None, **kwargs):
	"""
	Dayton Track-by-Date gateway.

	POST /api/method/ltl_quote.api.flowwolf.track_by_date

	Body:
	    {
	        "start": "2026-07-01T00:00:00Z",
	        "end": "2026-07-20T23:59:59Z",
	        "customer": "0055666"
	    }
	"""
	headers, body = _read_request_context()
	request = _merge_flowwolf_request(payload, **kwargs)
	status = "Queued"
	response_payload: dict = {}
	carrier_id = "DAYTON"

	try:
		start = request.get("start") or request.get("start_date")
		end = request.get("end") or request.get("end_date")
		customer = request.get("customer") or request.get("customer_code")
		if not start or not end:
			frappe.throw("Provide start and end (ISO timestamp or YYYY-MM-DD).")

		from ltl_quote.carrier_network.adapters.dayton import fetch_dayton_tracking_by_date

		raw = fetch_dayton_tracking_by_date(start, end, customer)
		if isinstance(raw, dict) and raw.get("status") == "error":
			frappe.throw(raw.get("text") or f"Dayton ByDate failed ({raw.get('code')})")

		results = raw.get("results") if isinstance(raw, dict) else raw
		if not isinstance(results, list):
			results = []
		enriched = _enrich_dayton_results_with_local_shipments(results)
		response_payload = {
			"status": "success",
			"engine": FLOWWOLF_ENGINE,
			"customer": (raw.get("customer") if isinstance(raw, dict) else None) or customer,
			"start": (raw.get("start") if isinstance(raw, dict) else None) or start,
			"end": (raw.get("end") if isinstance(raw, dict) else None) or end,
			"traceId": raw.get("traceId") if isinstance(raw, dict) else None,
			"count": len(enriched),
			"results": enriched,
		}
		status = "Quotes Received" if enriched else "No Quotes Received"

	except frappe.ValidationError as e:
		frappe.local.response["http_status_code"] = 400
		status = "API Error"
		response_payload = {"status": "error", "engine": FLOWWOLF_ENGINE, "message": str(e)}
	except Exception as e:
		frappe.log_error(message=frappe.get_traceback(), title="FlowWolf track_by_date API Error")
		status = "API Error"
		response_payload = {"status": "error", "engine": FLOWWOLF_ENGINE, "message": str(e)}
	finally:
		log_body = {**(body or {}), "api_url": FLOWWOLF_TRACK_BY_DATE_ENDPOINT, **request}
		log_api_transaction(headers, log_body, response_payload, status, carrier_id)

	return response_payload


@frappe.whitelist(allow_guest=False)
def track_pending(payload=None, **kwargs):
	"""
	Dayton Track Pending Shipments gateway.

	POST /api/method/ltl_quote.api.flowwolf.track_pending

	Body (optional):
	    { "customer": "0055666" }
	"""
	headers, body = _read_request_context()
	request = _merge_flowwolf_request(payload, **kwargs)
	status = "Queued"
	response_payload: dict = {}
	carrier_id = "DAYTON"

	try:
		customer = request.get("customer") or request.get("customer_code")
		from ltl_quote.carrier_network.adapters.dayton import fetch_dayton_pending_shipments

		raw = fetch_dayton_pending_shipments(customer)
		if isinstance(raw, dict) and raw.get("status") == "error":
			frappe.throw(raw.get("text") or f"Dayton Pending failed ({raw.get('code')})")

		results = raw.get("results") if isinstance(raw, dict) else raw
		if not isinstance(results, list):
			results = []
		enriched = _enrich_dayton_results_with_local_shipments(results)
		response_payload = {
			"status": "success",
			"engine": FLOWWOLF_ENGINE,
			"customer": (raw.get("customer") if isinstance(raw, dict) else None) or customer,
			"traceId": raw.get("traceId") if isinstance(raw, dict) else None,
			"count": len(enriched),
			"results": enriched,
		}
		status = "Quotes Received" if enriched else "No Quotes Received"

	except frappe.ValidationError as e:
		frappe.local.response["http_status_code"] = 400
		status = "API Error"
		response_payload = {"status": "error", "engine": FLOWWOLF_ENGINE, "message": str(e)}
	except Exception as e:
		frappe.log_error(message=frappe.get_traceback(), title="FlowWolf track_pending API Error")
		status = "API Error"
		response_payload = {"status": "error", "engine": FLOWWOLF_ENGINE, "message": str(e)}
	finally:
		log_body = {**(body or {}), "api_url": FLOWWOLF_TRACK_PENDING_ENDPOINT, **request}
		log_api_transaction(headers, log_body, response_payload, status, carrier_id)

	return response_payload


PLACEHOLDER_CARRIER_QUOTE_IDS = frozenset(
	{
		"paste_from_rates_response",
		"paste-from-rates-response",
		"carrier_quote_id",
		"your_carrier_quote_id",
		"todo",
		"string",
		"null",
		"none",
	}
)


def _normalize_carrier_quote_id(carrier_quote_id: str | None) -> str | None:
	"""Return a usable quote id, or None for empty / placeholder values."""
	wanted = str(carrier_quote_id or "").strip()
	if not wanted:
		return None
	if wanted.lower() in PLACEHOLDER_CARRIER_QUOTE_IDS:
		return None
	return wanted


def _resolve_quote_row_index(
	quote_doc,
	carrier_code: str | None = None,
	quote_row_idx: int | None = None,
	carrier_quote_id: str | None = None,
) -> int:
	carrier_id = resolve_carrier_id(carrier_code) if carrier_code else None
	wanted_quote_id = _normalize_carrier_quote_id(carrier_quote_id)

	if wanted_quote_id:
		matches = []
		for idx, row in enumerate(quote_doc.carrier_quotes):
			row_id = str(row.carrier_quote_id or "").strip()
			if not row_id:
				continue
			if row_id == wanted_quote_id or row_id.replace("ABF-", "") == wanted_quote_id.replace(
				"ABF-", ""
			):
				if carrier_id and row.carrier != carrier_id:
					continue
				matches.append(idx)
		if len(matches) == 1:
			if quote_row_idx is not None and int(quote_row_idx) != matches[0]:
				frappe.throw(
					f"carrier_quote_id maps to quote row {matches[0]}, "
					f"but quote_row_idx {int(quote_row_idx)} was provided."
				)
			return matches[0]
		if len(matches) > 1 and quote_row_idx is None and not carrier_id:
			frappe.throw("Multiple quote lines match carrier_quote_id. Provide carrier_preference.")
		if matches:
			return matches[0]
		# Real id provided but no match — fail clearly instead of silently auto-picking.
		frappe.throw(f"No quote line found for carrier_quote_id {wanted_quote_id}.")

	if carrier_id:
		for idx, row in enumerate(quote_doc.carrier_quotes):
			if row.carrier == carrier_id:
				if quote_row_idx is not None and int(quote_row_idx) != idx:
					frappe.throw(
						f"carrier_code {carrier_code} maps to quote row {idx}, "
						f"but quote_row_idx {int(quote_row_idx)} was provided."
					)
				return idx
		frappe.throw(f"No quote line found for carrier {carrier_code}.")

	if quote_row_idx is not None:
		idx = int(quote_row_idx)
		if idx < 0 or idx >= len(quote_doc.carrier_quotes):
			frappe.throw("Select a valid carrier quote before booking.")
		return idx

	if len(quote_doc.carrier_quotes) == 1:
		return 0

	# Prefer an already-selected row on the quote request when still valid.
	selected_raw = str(getattr(quote_doc, "selected_carrier_quote", None) or "").strip()
	if selected_raw.isdigit():
		selected_idx = int(selected_raw)
		if 0 <= selected_idx < len(quote_doc.carrier_quotes):
			return selected_idx

	# Default: cheapest quote (FlowWolf multicarrier without preference).
	return min(
		range(len(quote_doc.carrier_quotes)),
		key=lambda i: flt(quote_doc.carrier_quotes[i].total_charge),
	)


def _read_request_context() -> tuple[dict, dict]:
	headers = dict(frappe.request.headers) if getattr(frappe, "request", None) else {}
	body: dict = {}

	if getattr(frappe, "request", None):
		if getattr(frappe.request, "json", None):
			raw = frappe.request.json
			if isinstance(raw, dict):
				body = raw
		elif frappe.request.data:
			try:
				raw = json.loads(frappe.request.data.decode("utf-8"))
				if isinstance(raw, dict):
					body = raw
			except (ValueError, UnicodeDecodeError):
				body = dict(frappe.local.form_dict)
		else:
			body = dict(frappe.local.form_dict)
	else:
		body = dict(frappe.local.form_dict)

	body.pop("cmd", None)
	return headers, body


def _broadcast_carrier_rates(carrier_docs, shipment_request) -> tuple[list, list]:
	"""Ping each enabled carrier adapter and collect quotes + errors."""
	aggregated_quotes = []
	errors = []

	for carrier_doc in carrier_docs:
		carrier_id = carrier_doc.name
		try:
			adapter = get_adapter(carrier_doc)
			quote = adapter.get_rates(shipment_request)

			if quote and not quote.error and quote.total_charge and quote.total_charge > 0:
				# Persist against LTL Carrier document name (Link field on quote lines).
				quote.carrier_code = carrier_doc.name
				aggregated_quotes.append(quote)
			elif quote and quote.error:
				errors.append({"carrier": carrier_doc.carrier_name or carrier_id, "error": quote.error})
			else:
				errors.append(
					{
						"carrier": carrier_doc.carrier_name or carrier_id,
						"error": "Carrier returned no quote object",
					}
				)
		except Exception as ex:
			frappe.log_error(message=str(ex), title=f"FlowWolf Adapter Error: {carrier_id}")
			errors.append({"carrier": carrier_doc.carrier_name or carrier_id, "error": f"Adapter crash: {ex}"})

	return aggregated_quotes, errors


def _persist_carrier_quotes(quote_request, quotes, errors: list | None = None) -> None:
	"""Write successful CarrierRateQuote rows onto the LTL Quote Request for later BOL booking."""
	quote_currency = get_quote_currency()
	quote_request.carrier_quotes = []
	for q in sorted(quotes, key=lambda x: x.total_charge):
		est_delivery = add_days(getdate(), q.transit_days) if q.transit_days else None
		quote_request.append(
			"carrier_quotes",
			{
				"carrier": q.carrier_code,
				"carrier_name": q.carrier_name,
				"carrier_quote_id": q.carrier_quote_id,
				"status": "Received",
				"total_charge": q.total_charge,
				"currency": q.currency or quote_currency,
				"transit_days": q.transit_days,
				"estimated_delivery_date": est_delivery,
				"linehaul_charge": q.linehaul_charge,
				"fuel_surcharge": q.fuel_surcharge,
				"accessorial_charge": q.accessorial_charge,
				"reliability_score": q.reliability_score,
				"service_level": q.service_level,
				"accessorial_breakdown": json.dumps(q.accessorial_breakdown)
				if q.accessorial_breakdown
				else None,
				"raw_response": json.dumps(q.raw_response, indent=2) if q.raw_response else None,
			},
		)

	quote_request.status = "Quoted" if quotes else "Error"
	quote_request.aggregated_on = now_datetime()
	if errors:
		quote_request.error_log = "\n".join(
			f"{item['carrier']}: {item['error']}" if isinstance(item, dict) else str(item)
			for item in errors
		)
	quote_request.save(ignore_permissions=True)
	frappe.db.commit()


def _build_flowwolf_recommendations(ranked_quotes: list[dict]) -> dict:
	if not ranked_quotes:
		return {"cheapest": None, "fastest": None, "best_value": None}

	cheapest = min(ranked_quotes, key=lambda q: q["total_cost"])
	fastest = min(ranked_quotes, key=lambda q: q.get("transit_days") or 999)

	best_value = ranked_quotes[0]
	for quote in ranked_quotes:
		if "Best Value" in (quote.get("tags") or []):
			best_value = quote
			break

	def _label(quote: dict, tag: str) -> str:
		return (
			f"[{tag}] {quote.get('carrier')} — ${flt(quote.get('total_cost'), 2)} "
			f"| {quote.get('transit_days')} days"
		)

	return {
		"cheapest": _label(cheapest, "Cheapest"),
		"fastest": _label(fastest, "Fastest"),
		"best_value": _label(best_value, "Best Value"),
	}

