"""
FlowWolf Unified Multi-Carrier LTL Rating Gateway API

POST /api/method/ltl_quote.api.flowwolf.get_rates
"""

from __future__ import annotations

import json

import frappe
from frappe.utils import flt

from ltl_quote.api.carrier_mapping import load_carrier_for_rating, resolve_carrier_id
from ltl_quote.api.payload import parse_rating_payload
from ltl_quote.api.quote import _build_shipment_request_from_payload, _create_quote_request
from ltl_quote.booking.executor import ShipmentExecutor
from ltl_quote.carrier_network.registry import get_adapter
from ltl_quote.decision_engine.recommender import rank_quotes
from ltl_quote.utils.location import enrich_location_fields, resolve_us_location
from ltl_quote.utils.transaction_log import log_api_transaction

FLOWWOLF_API_ENDPOINT = "/api/method/ltl_quote.api.flowwolf.get_rates"
FLOWWOLF_ENGINE = "FlowWolf Aggregator Engine v1"


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
def book_carrier_quote(
	quote_request_id: str,
	carrier_code: str | None = None,
	quote_row_idx: int | None = None,
	transit_days: int | None = None,
	is_test: bool = False,
):
	"""Server-side gateway to book an engineered quote from the Frappe desk UI."""
	if not frappe.db.exists("LTL Quote Request", quote_request_id):
		frappe.throw(f"Quote Request record {quote_request_id} not found.")

	quote_doc = frappe.get_doc("LTL Quote Request", quote_request_id)
	if quote_doc.status == "Booked":
		frappe.throw(f"Quote Request {quote_request_id} is already booked.")

	if not quote_doc.carrier_quotes:
		frappe.throw("No carrier quotes are available on this request. Fetch rates before booking.")

	row_idx = _resolve_quote_row_index(quote_doc, carrier_code, quote_row_idx)
	selected = quote_doc.carrier_quotes[row_idx]
	carrier_id = resolve_carrier_id(carrier_code) if carrier_code else selected.carrier
	if not carrier_id:
		carrier_id = selected.carrier

	carrier_docs, carrier_label = load_carrier_for_rating(carrier_id)
	carrier_doc = carrier_docs[0]
	if carrier_doc.connector_type == "Mock" and carrier_code and carrier_code.upper() != "MOCK":
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

	try:
		result = ShipmentExecutor(quote_doc).book(is_test=is_test)
	except Exception:
		frappe.log_error(message=frappe.get_traceback(), title="FlowWolf Front-End Booking Error")
		raise

	carrier_name = carrier_doc.carrier_name or carrier_label
	return {
		"status": "success",
		"message": (
			f"Successfully booked with {carrier_name}! "
			f"BOL Generated: {result.get('bol_number')}"
		),
		"data": {
			**result,
			"carrier_code": carrier_id,
			"carrier_name": carrier_name,
			"origin_city": origin_city,
			"origin_state": origin_state,
		},
	}


def _resolve_quote_row_index(quote_doc, carrier_code: str | None, quote_row_idx: int | None) -> int:
	carrier_id = resolve_carrier_id(carrier_code) if carrier_code else None

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

	frappe.throw("Select a carrier quote before booking.")


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

