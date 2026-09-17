# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

import json

import frappe
from frappe.utils import now_datetime

API_GATEWAY_ENDPOINT = "/api/method/ltl_quote.api.quote.get_ltl_rates"

LOG_CARRIER_LABELS = {
	"DAYTON": "Dayton Freight",
	"ARCB": "ArcBest",
	"TFORCE": "TForce Freight",
	"SMC3": "SMC3",
	"MOCK": "Mock Carriers",
	"Multi-Carrier": "Multi-Carrier",
}


ALLOWED_LOG_STATUSES = frozenset(
	{
		"Queued",
		"Quotes Received",
		"No Quotes Received",
		"Booked",
		"Already Booked",
		"API Error",
		"Connection Failed",
		"Tracked",
		"Dispatched",
		"Cancelled",
	}
)

LOG_STATUS_ALIASES = {
	"Tracked": "Quotes Received",
	"Dispatched": "Booked",
	"Cancelled": "Booked",
	"Assigned": "Booked",
	"Success": "Quotes Received",
}


def coerce_log_status(status: str) -> str:
	value = str(status or "").strip()
	if value in ALLOWED_LOG_STATUSES:
		return value
	return LOG_STATUS_ALIASES.get(value, "API Error")


def log_api_transaction(headers, body, response_payload, status, carrier_id):
	"""
	Save the gateway API interaction into LTL Carrier Transaction Log.

	Signature matches ltl_quote.api.quote.get_ltl_rates import:
	    log_api_transaction(headers, body, response_payload, status, carrier_id)
	"""
	try:
		body = body or {}
		carrier_label = LOG_CARRIER_LABELS.get(carrier_id, carrier_id or "Dayton Freight")
		api_url = body.get("api_url") or body.get("api_endpoint") or API_GATEWAY_ENDPOINT
		log_status = coerce_log_status(status)

		log_doc = frappe.get_doc(
			{
				"doctype": "LTL Carrier Transaction Log",
				"carrier_name": carrier_label,
				"direction": "Sent",
				"action_method": "POST",
				"api_endpoint": api_url,
				"status": log_status,
				"origin_zip": body.get("origin_zip"),
				"destination_zip": body.get("destination_zip"),
				"timestamp": now_datetime(),
				"headers": json.dumps(_sanitize_headers(headers), indent=2),
				"request_payload": json.dumps(body, indent=2, default=str),
				"response_payload": json.dumps(response_payload, indent=2, default=str),
			}
		)
		log_doc.insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception as log_ex:
		# Keep booking/rate API responses clean if logging fails.
		frappe.clear_messages()
		frappe.logger().error(f"Failed to write LTL Carrier Transaction Log: {log_ex}")


def _format_json(value) -> str:
	if value is None:
		return ""
	if isinstance(value, (dict, list)):
		return json.dumps(value, indent=2, default=str)
	if isinstance(value, str):
		try:
			return json.dumps(json.loads(value), indent=2)
		except (ValueError, TypeError):
			return value
	return str(value)


def _sanitize_headers(headers) -> dict:
	safe = dict(headers or {})
	if safe.get("Authorization"):
		safe["Authorization"] = "token ***"
	return safe


def log_carrier_transaction(
	carrier: str,
	method: str,
	url: str,
	origin: str,
	dest: str,
	headers,
	request_body,
	response_text: str,
	status: str,
	direction: str = "Sent",
):
	"""Persist a carrier adapter API round-trip into LTL Carrier Transaction Log."""
	try:
		formatted_headers = _format_json(_sanitize_headers(headers))
		formatted_req = _format_json(request_body)
		formatted_res = _format_json(response_text)

		log_doc = frappe.get_doc(
			{
				"doctype": "LTL Carrier Transaction Log",
				"carrier_name": carrier,
				"direction": direction,
				"action_method": method if str(method or "").upper() in {"POST", "GET", "PUT"} else "POST",
				"api_endpoint": url,
				"status": coerce_log_status(status),
				"origin_zip": origin,
				"destination_zip": dest,
				"timestamp": now_datetime(),
				"headers": formatted_headers,
				"request_payload": formatted_req,
				"response_payload": formatted_res,
			}
		)
		log_doc.insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception as log_ex:
		frappe.clear_messages()
		frappe.logger().error(f"Failed to write LTL Carrier Transaction Log: {log_ex}")
