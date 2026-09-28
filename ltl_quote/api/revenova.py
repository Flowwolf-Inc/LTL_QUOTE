# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""Revenova inbound LTL Quote routing.

POST /api/v1/revenova/ltl_quote
POST /api/method/ltl_quote.api.revenova.ltl_quote
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe.utils import cint

from ltl_quote.api.sync import _authenticate_webhook, _merge_request, handle_carrier_quote
from ltl_quote.freight.doctype.ltl_integration_settings.ltl_integration_settings import (
	get_customer_mapping,
	inbound_routing_enabled,
)

CUSTOMER_KEYS = (
	"customer",
	"customer_id",
	"customer_code",
	"Customer",
	"account",
	"AccountId",
	"tenant_code",
)
SKIP_FIELDS = {
	"name",
	"owner",
	"creation",
	"modified",
	"modified_by",
	"docstatus",
	"idx",
	"doctype",
	"parent",
	"parenttype",
	"parentfield",
	"naming_series",
	"customer",
	"customer_id",
	"customer_code",
	"Customer",
	"account",
	"AccountId",
	"tenant_code",
	"cmd",
	"data",
}
V1_PATH = "/api/v1/revenova/ltl_quote"
V1_METHOD_PATH = "/api/method/ltl_quote.api.revenova.ltl_quote"


def rewrite_revenova_v1_path() -> None:
	"""Map POST /api/v1/revenova/ltl_quote onto the whitelist method.

	Frappe's global auth treats Authorization: Bearer as OAuth/API keys.
	Move a matching webhook Bearer onto X-Revenova-Token so allow_guest
	inbound can authenticate via _authenticate_webhook instead.
	"""
	request = getattr(frappe, "request", None)
	if not request:
		return
	path = str(getattr(request, "path", None) or request.environ.get("PATH_INFO") or "").rstrip("/")
	if path == V1_PATH:
		request.environ["PATH_INFO"] = V1_METHOD_PATH
		path = V1_METHOD_PATH
	if path != V1_METHOD_PATH:
		return
	_move_bearer_to_revenova_token(request)


def _move_bearer_to_revenova_token(request) -> None:
	environ = request.environ
	auth = str(environ.get("HTTP_AUTHORIZATION") or "")
	if not auth.lower().startswith("bearer "):
		return
	token = auth[7:].strip()
	if token and not environ.get("HTTP_X_REVENOVA_TOKEN"):
		environ["HTTP_X_REVENOVA_TOKEN"] = token
	environ.pop("HTTP_AUTHORIZATION", None)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def ltl_quote(payload=None, **kwargs):
	"""Route an inbound Revenova quote payload to the mapped customer DocType."""
	return handle_inbound_ltl_quote(payload, **kwargs)


def handle_inbound_ltl_quote(payload=None, **kwargs):
	"""Core inbound router — callable from tests without whitelist wrappers."""
	try:
		_authenticate_webhook()
		body = _payload_body(payload, kwargs)
		customer_id = _extract_customer_id(body)
		if not customer_id:
			return _http(
				400,
				{
					"status": "error",
					"message": "customer is required.",
				},
			)

		if not inbound_routing_enabled():
			return _http(
				403,
				{
					"status": "rejected",
					"customer": customer_id,
					"message": "Inbound LTL Quote routing is disabled.",
				},
			)

		mapping = get_customer_mapping(customer_id)
		if not mapping:
			return _http(
				400,
				{
					"status": "error",
					"customer": customer_id,
					"message": f"No mapping exists for customer {customer_id}.",
				},
			)
		if mapping["disabled"]:
			return _http(
				403,
				{
					"status": "rejected",
					"customer": customer_id,
					"message": "Customer mapping is disabled.",
				},
			)

		result = _insert_mapped_doc(mapping["target_doctype"], body)
		return _http(
			200,
			{
				"status": "success",
				"customer": customer_id,
				"doctype": mapping["target_doctype"],
				"name": result.get("name"),
				"action": result.get("action"),
			},
		)
	except frappe.AuthenticationError as exc:
		return _http(401, {"status": "error", "message": str(exc)})
	except frappe.DoesNotExistError as exc:
		return _http(400, {"status": "error", "message": str(exc)})
	except frappe.ValidationError as exc:
		frappe.db.rollback()
		return _http(400, {"status": "error", "message": str(exc)})
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Revenova LTL Quote", message=frappe.get_traceback())
		return _http(500, {"status": "error", "message": "Unable to process inbound LTL Quote."})


def _payload_body(payload, kwargs: dict) -> dict:
	request = _merge_request(payload, kwargs)
	for nested_key in ("data", "payload"):
		nested = request.get(nested_key)
		if isinstance(nested, str):
			try:
				nested = json.loads(nested)
			except (ValueError, TypeError):
				nested = None
		if isinstance(nested, dict):
			merged = dict(nested)
			for key, value in request.items():
				if key in ("data", "payload") or key in merged:
					continue
				merged[key] = value
			return merged
	return request if isinstance(request, dict) else {}


def _extract_customer_id(body: dict) -> str:
	for key in CUSTOMER_KEYS:
		value = str((body or {}).get(key) or "").strip()
		if value:
			return value
	return ""


def _insert_mapped_doc(target_doctype: str, body: dict) -> dict[str, Any]:
	if not frappe.db.exists("DocType", target_doctype):
		frappe.throw(f"DocType {target_doctype} does not exist.")
	if target_doctype == "Carrier Quote":
		result = handle_carrier_quote(body)
		frappe.db.commit()
		return result

	meta = frappe.get_meta(target_doctype)
	if meta.istable or meta.issingle:
		frappe.throw(f"{target_doctype} cannot be an inbound target.")

	doc = frappe.new_doc(target_doctype)
	_apply_matching_fields(doc, meta, body)
	if meta.get_field("raw_payload"):
		doc.set("raw_payload", json.dumps(body, default=str))
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return {"name": doc.name, "action": "created"}


def _apply_matching_fields(doc, meta, body: dict) -> None:
	for key, value in (body or {}).items():
		if key in SKIP_FIELDS or value in (None, ""):
			continue
		if not meta.get_field(key):
			continue
		field = meta.get_field(key)
		if field.fieldtype in {"Table", "Table MultiSelect", "Attach", "Attach Image", "Password"}:
			continue
		doc.set(key, value)


def _http(status_code: int, payload: dict) -> dict:
	frappe.local.response["http_status_code"] = status_code
	return payload
