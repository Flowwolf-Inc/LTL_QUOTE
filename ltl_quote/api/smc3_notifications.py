# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""SMC3 Notifications v1 — list and delete callback endpoints."""

from __future__ import annotations

import frappe

from ltl_quote.carrier_network.smc3_token import AUTH_USER_MESSAGE, SMC3AuthError


@frappe.whitelist()
def list_notification_callbacks(carrier=None):
	"""GET /notifications/v1/app/callback-endpoint."""
	adapter = _adapter(carrier)
	result = adapter.list_status_callback_endpoints()
	callbacks = parse_notification_callbacks(result.get("raw") or result)
	return {
		"status": "success",
		"ok": True,
		"callbacks": callbacks,
		"transaction_id": str(result.get("transaction_id") or "").strip(),
		"message": result.get("message") or "Notification callbacks retrieved.",
		"raw": result.get("raw") or {},
	}


@frappe.whitelist()
def delete_notification_callback(callback_id, carrier=None):
	"""DELETE /notifications/v1/app/callback-endpoint/{callback_id}."""
	callback_id = str(callback_id or "").strip()
	if not callback_id:
		frappe.throw("A callback endpoint id is required.")
	adapter = _adapter(carrier)
	result = adapter.delete_status_callback_endpoint(callback_id)
	return {
		"status": "success",
		"ok": True,
		"callback_id": callback_id,
		"transaction_id": str(result.get("transaction_id") or "").strip(),
		"message": result.get("message") or "Notification callback deleted.",
		"raw": result.get("raw") or {},
	}


def parse_notification_callbacks(data: dict | None) -> list[dict]:
	"""Normalize a Notifications v1 list payload into callback rows."""
	data = data if isinstance(data, dict) else {}
	raw = (
		data.get("callbackEndpoints")
		or data.get("callbackEndpoint")
		or data.get("callbacks")
		or data.get("endpoints")
		or data.get("callback-endpoint")
		or []
	)
	if isinstance(raw, dict):
		raw = raw.get("callbackEndpoints") or raw.get("callbackEndpoint") or raw.get("callbacks") or [raw]
	if not isinstance(raw, list):
		raw = [raw] if raw else []
	rows = []
	seen = set()
	for item in raw:
		parsed = _parse_callback_row(item)
		if not parsed:
			continue
		key = parsed["callback_id"] or parsed["endpoint"]
		if key and key in seen:
			continue
		if key:
			seen.add(key)
		rows.append(parsed)
	return rows


def _parse_callback_row(item) -> dict | None:
	if isinstance(item, str):
		text = item.strip()
		if not text:
			return None
		return {
			"callback_id": text,
			"endpoint": text if text.startswith("http") else "",
			"effective_date": "",
			"service": "STATUS",
			"transaction_id": "",
		}
	if not isinstance(item, dict):
		return None
	callback_id = str(
		item.get("id")
		or item.get("callbackId")
		or item.get("callbackEndpointId")
		or item.get("endpointId")
		or item.get("transactionId")
		or ""
	).strip()
	endpoint = str(
		item.get("endpoint")
		or item.get("url")
		or item.get("callbackUrl")
		or item.get("callbackURL")
		or item.get("callbackEndpoint")
		or ""
	).strip()
	if not callback_id and not endpoint:
		return None
	return {
		"callback_id": callback_id,
		"endpoint": endpoint,
		"effective_date": str(item.get("effectiveDate") or item.get("effective_date") or "").strip(),
		"service": str(item.get("service") or "STATUS").strip().upper() or "STATUS",
		"transaction_id": str(item.get("transactionId") or item.get("transaction_id") or "").strip(),
	}


def _adapter(carrier=None):
	from ltl_quote.carrier_network.adapters.smc3 import SMC3CarrierAdapter

	name = str(carrier or "").strip() or "SMC3"
	if not frappe.db.exists("LTL Carrier", name):
		if name != "SMC3" and frappe.db.exists("LTL Carrier", "SMC3"):
			name = "SMC3"
		else:
			frappe.throw("SMC3 carrier record not found.")
	doc = frappe.get_doc("LTL Carrier", name)
	frappe.has_permission("LTL Carrier", "write", doc=doc, throw=True)
	try:
		return SMC3CarrierAdapter(doc)
	except SMC3AuthError:
		frappe.throw(AUTH_USER_MESSAGE)
