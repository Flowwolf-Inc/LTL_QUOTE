# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""Bi-directional Revenova (Salesforce) sync — single-endpoint architecture.

Inbound::
    POST /api/method/ltl_quote.api.sync.revenova_webhook
    {"event_type": "carrier_quote", "data": {...}}

Outbound::
    Carrier Quote on_update → enqueue dispatch_to_revenova
    POST {revenova_api_url}  {"event_type": "...", "doc_name": "...", "data": {...}}

site_config.json (optional, wins over LTL Platform Settings)::
    revenova_enabled, revenova_api_url, revenova_auth_token, revenova_webhook_secret
"""

from __future__ import annotations

import json
from typing import Any

import frappe
import requests
from frappe.utils import cint, flt, now_datetime

CARRIER_QUOTE_DOCTYPE = "Carrier Quote"
SETTINGS_DOCTYPE = "LTL Platform Settings"
LOGGER = "revenova"

# Salesforce / Revenova payload keys → Carrier Quote fieldnames.
CARRIER_QUOTE_FIELD_MAP = {
	"revenova_id": "revenova_id",
	"Id": "revenova_id",
	"id": "revenova_id",
	"quote_request": "quote_request",
	"quote_request_id": "quote_request",
	"carrier": "carrier",
	"carrier_name": "carrier_name",
	"quoted_scac": "quoted_scac",
	"scac": "quoted_scac",
	"carrier_quote_id": "carrier_quote_id",
	"service_level": "service_level",
	"status": "status",
	"total_charge": "total_charge",
	"total_cost": "total_charge",
	"currency": "currency",
	"transit_days": "transit_days",
	"origin_zip": "origin_zip",
	"destination_zip": "destination_zip",
}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def revenova_webhook(payload=None, **kwargs):
	"""Single inbound webhook for Quotes, BOL, and shipment status events."""
	try:
		request = _merge_request(payload, kwargs)
		_authenticate_webhook()

		event_type = str(request.get("event_type") or "").strip()
		data = request.get("data")
		if data is None:
			data = {k: v for k, v in request.items() if k != "event_type"}
		if not isinstance(data, dict):
			return _http(400, {"status": "error", "message": "data must be an object."})
		if not event_type:
			return _http(400, {"status": "error", "message": "event_type is required."})

		handler = EVENT_HANDLERS.get(event_type)
		if handler is None:
			return _http(
				400,
				{
					"status": "error",
					"message": f"Unsupported event_type '{event_type}'.",
					"supported": sorted(EVENT_HANDLERS.keys()),
				},
			)

		frappe.flags.in_revenova_sync = True
		result = handler(data)
		frappe.db.commit()
		return _http(
			200,
			{
				"status": "success",
				"event_type": event_type,
				"result": result,
			},
		)
	except frappe.AuthenticationError as exc:
		return _http(401, {"status": "error", "message": str(exc)})
	except frappe.ValidationError as exc:
		frappe.db.rollback()
		return _http(400, {"status": "error", "message": str(exc)})
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Revenova Webhook", message=frappe.get_traceback())
		return _http(500, {"status": "error", "message": "Unable to process Revenova webhook."})
	finally:
		frappe.flags.in_revenova_sync = False


def handle_carrier_quote(data: dict) -> dict:
	"""Create or update Carrier Quote from a Revenova payload. Blocks outbound echo."""
	data = data or {}
	revenova_id = _first_text(data, "revenova_id", "Id", "id")
	if not revenova_id:
		frappe.throw("revenova_id is required for carrier_quote events.")

	existing = frappe.db.exists(CARRIER_QUOTE_DOCTYPE, {"revenova_id": revenova_id})
	doc = frappe.get_doc(CARRIER_QUOTE_DOCTYPE, existing) if existing else frappe.new_doc(CARRIER_QUOTE_DOCTYPE)
	if not existing:
		doc.revenova_id = revenova_id

	_apply_carrier_quote_fields(doc, data)
	_sanitize_links(doc)
	doc.synced_from_revenova = 1
	doc.flags.synced_from_revenova = True
	doc.flags.ignore_revenova_sync = True
	doc.last_synced_on = now_datetime()
	doc.sync_status = "Synced"
	doc.last_sync_error = None
	doc.save(ignore_permissions=True)

	# Keep the check off after the inbound save so a later local edit still publishes.
	frappe.db.set_value(CARRIER_QUOTE_DOCTYPE, doc.name, "synced_from_revenova", 0, update_modified=False)

	return {
		"name": doc.name,
		"revenova_id": revenova_id,
		"action": "updated" if existing else "created",
	}


def handle_bol(data: dict) -> dict:
	"""Placeholder — map Revenova BOL events onto LTL Shipment."""
	return {"status": "ignored", "event_type": "bol"}


def handle_shipment_status(data: dict) -> dict:
	"""Placeholder — map Revenova tracking events onto LTL Shipment."""
	return {"status": "ignored", "event_type": "shipment_status"}


EVENT_HANDLERS = {
	"carrier_quote": handle_carrier_quote,
	"bol": handle_bol,
	"shipment_status": handle_shipment_status,
}


def on_carrier_quote_update(doc, method=None):
	"""DocType hook: enqueue an outbound Revenova callout unless this save came inbound."""
	if should_skip_outbound(doc):
		return

	config = get_revenova_config()
	if not config.get("enabled"):
		return
	if not config.get("api_url"):
		frappe.logger(LOGGER).warning("Revenova outbound skipped: API URL is not configured.")
		return

	frappe.enqueue(
		"ltl_quote.api.sync.dispatch_to_revenova",
		queue="short",
		enqueue_after_commit=True,
		event_type="carrier_quote",
		doc_name=doc.name,
		payload=serialize_carrier_quote(doc),
	)


def should_skip_outbound(doc) -> bool:
	"""True when an inbound Revenova write (or a nested sync save) caused this update."""
	if getattr(doc.flags, "ignore_revenova_sync", False):
		return True
	if getattr(doc.flags, "synced_from_revenova", False):
		return True
	if cint(getattr(doc, "synced_from_revenova", 0)):
		return True
	if getattr(frappe.flags, "in_revenova_sync", False):
		return True
	return False


def dispatch_to_revenova(event_type: str, doc_name: str, payload: dict | None = None):
	"""Background worker: POST one event to Revenova's Apex REST endpoint."""
	config = get_revenova_config()
	if not config.get("enabled"):
		return {"status": "skipped", "reason": "disabled"}

	url = str(config.get("api_url") or "").strip()
	if not url:
		return {"status": "skipped", "reason": "missing_url"}

	body = {
		"event_type": event_type,
		"source": "frappe",
		"doc_name": doc_name,
		"data": payload or {},
	}
	headers = {
		"Authorization": f"Bearer {config.get('auth_token') or ''}",
		"Content-Type": "application/json",
		"X-Frappe-Source": "ltl_quote",
		"X-Frappe-Doc": doc_name,
	}

	try:
		response = requests.post(url, json=body, headers=headers, timeout=30)
		ok = 200 <= response.status_code < 300
		parsed = _safe_json(response)
		if ok:
			_mark_sync_success(doc_name, parsed)
			return {"status": "success", "http_status": response.status_code, "response": parsed}
		message = _error_message(response, parsed)
		_mark_sync_error(doc_name, message)
		frappe.log_error(
			title=f"Revenova outbound {event_type} {doc_name}",
			message=f"{response.status_code} {url}\n{message}",
		)
		return {"status": "error", "http_status": response.status_code, "message": message}
	except requests.RequestException as exc:
		_mark_sync_error(doc_name, str(exc))
		frappe.log_error(
			title=f"Revenova outbound {event_type} {doc_name}",
			message=frappe.get_traceback(),
		)
		return {"status": "error", "message": str(exc)}


def serialize_carrier_quote(doc) -> dict:
	"""Build the outbound data object Revenova's Apex class expects."""
	return {
		"frappe_name": doc.name,
		"revenova_id": getattr(doc, "revenova_id", None) or None,
		"quote_request": getattr(doc, "quote_request", None),
		"carrier": getattr(doc, "carrier", None),
		"carrier_name": getattr(doc, "carrier_name", None),
		"quoted_scac": getattr(doc, "quoted_scac", None),
		"carrier_quote_id": getattr(doc, "carrier_quote_id", None),
		"service_level": getattr(doc, "service_level", None),
		"status": getattr(doc, "status", None),
		"total_charge": flt(getattr(doc, "total_charge", None)),
		"currency": getattr(doc, "currency", None),
		"transit_days": cint(getattr(doc, "transit_days", None)),
		"origin_zip": getattr(doc, "origin_zip", None),
		"destination_zip": getattr(doc, "destination_zip", None),
	}


def get_revenova_config() -> dict:
	"""Resolve URL/token from site_config first, then LTL Platform Settings."""
	settings = _platform_settings()
	enabled = _conf_flag("revenova_enabled")
	if enabled is None:
		enabled = bool(cint(getattr(settings, "revenova_enabled", 0))) if settings else False

	return {
		"enabled": bool(enabled),
		"api_url": _conf_or_setting("revenova_api_url", settings, "revenova_api_url"),
		"auth_token": _conf_or_password("revenova_auth_token", settings, "revenova_auth_token"),
		"webhook_secret": _conf_or_password("revenova_webhook_secret", settings, "revenova_webhook_secret"),
	}


def _sanitize_links(doc) -> None:
	"""Drop Link values that do not exist yet so inbound upserts are not blocked."""
	if getattr(doc, "quote_request", None) and not frappe.db.exists("LTL Quote Request", doc.quote_request):
		doc.quote_request = None
	if getattr(doc, "carrier", None) and not frappe.db.exists("LTL Carrier", doc.carrier):
		doc.carrier = None


def _apply_carrier_quote_fields(doc, data: dict) -> None:
	applied: set[str] = set()
	for source, fieldname in CARRIER_QUOTE_FIELD_MAP.items():
		if fieldname in applied or source not in data or data.get(source) in (None, ""):
			continue
		if fieldname == "revenova_id" and getattr(doc, "revenova_id", None):
			applied.add(fieldname)
			continue
		meta = getattr(doc, "meta", None)
		get_field = getattr(meta, "get_field", None) if meta is not None else None
		if callable(get_field) and get_field(fieldname) is None:
			continue
		value = data.get(source)
		if fieldname == "total_charge":
			value = flt(value)
		elif fieldname == "transit_days":
			value = cint(value)
		doc.set(fieldname, value)
		applied.add(fieldname)
	doc.raw_payload = json.dumps(data, default=str)


def _authenticate_webhook() -> None:
	secret = str(get_revenova_config().get("webhook_secret") or "").strip()
	skip_auth = cint(frappe.conf.get("revenova_skip_webhook_auth") or 0)
	if skip_auth:
		return
	if not secret:
		frappe.throw("Revenova webhook secret is not configured.", frappe.AuthenticationError)

	incoming = _bearer_token()
	if not incoming or incoming != secret:
		frappe.throw("Invalid Revenova webhook credentials.", frappe.AuthenticationError)


def _bearer_token() -> str:
	request = getattr(frappe, "request", None)
	if not request:
		return ""
	header = str(request.headers.get("Authorization") or request.headers.get("authorization") or "")
	if header.lower().startswith("bearer "):
		return header[7:].strip()
	return str(request.headers.get("X-Revenova-Token") or "").strip()


def _merge_request(payload, kwargs: dict) -> dict:
	body = _request_json()
	merged: dict[str, Any] = {}
	if isinstance(payload, dict):
		merged.update(payload)
	merged.update(body)
	for key, value in (kwargs or {}).items():
		if value is not None and key not in merged:
			merged[key] = value
	merged.pop("cmd", None)
	return merged


def _request_json() -> dict:
	request = getattr(frappe, "request", None)
	if not request:
		return {}
	if getattr(request, "json", None) and isinstance(request.json, dict):
		return request.json
	raw = getattr(request, "data", None)
	if not raw:
		return {}
	try:
		parsed = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
	except (ValueError, UnicodeDecodeError, AttributeError):
		return {}
	return parsed if isinstance(parsed, dict) else {}


def _platform_settings():
	try:
		if frappe.db.exists(SETTINGS_DOCTYPE, SETTINGS_DOCTYPE):
			return frappe.get_single(SETTINGS_DOCTYPE)
	except Exception:
		return None
	return None


def _conf_flag(key: str) -> bool | None:
	if key not in (frappe.conf or {}):
		return None
	return bool(cint(frappe.conf.get(key)))


def _conf_or_setting(conf_key: str, settings, fieldname: str) -> str:
	conf_value = str(frappe.conf.get(conf_key) or "").strip()
	if conf_value:
		return conf_value
	if settings:
		return str(settings.get(fieldname) or "").strip()
	return ""


def _conf_or_password(conf_key: str, settings, fieldname: str) -> str:
	conf_value = str(frappe.conf.get(conf_key) or "").strip()
	if conf_value:
		return conf_value
	if not settings:
		return ""
	try:
		return str(settings.get_password(fieldname) or "").strip()
	except Exception:
		return str(settings.get(fieldname) or "").strip()


def _first_text(data: dict, *keys: str) -> str:
	for key in keys:
		value = str(data.get(key) or "").strip()
		if value:
			return value
	return ""


def _safe_json(response) -> dict | None:
	try:
		parsed = response.json()
	except ValueError:
		return None
	return parsed if isinstance(parsed, dict) else None


def _error_message(response, parsed: dict | None) -> str:
	if parsed:
		return str(parsed.get("message") or parsed.get("error") or parsed)[:500]
	return (response.text or f"HTTP {response.status_code}")[:500]


def _mark_sync_success(doc_name: str, parsed: dict | None) -> None:
	if not doc_name or not frappe.db.exists(CARRIER_QUOTE_DOCTYPE, doc_name):
		return
	values = {
		"sync_status": "Synced",
		"last_synced_on": now_datetime(),
		"last_sync_error": None,
	}
	revenova_id = _first_text(parsed or {}, "revenova_id", "id", "Id")
	if revenova_id:
		values["revenova_id"] = revenova_id
	# db.set_value does not fire doc_events, so this cannot loop.
	frappe.db.set_value(CARRIER_QUOTE_DOCTYPE, doc_name, values, update_modified=False)


def _mark_sync_error(doc_name: str, message: str) -> None:
	if not doc_name or not frappe.db.exists(CARRIER_QUOTE_DOCTYPE, doc_name):
		return
	frappe.db.set_value(
		CARRIER_QUOTE_DOCTYPE,
		doc_name,
		{"sync_status": "Error", "last_sync_error": (message or "")[:140]},
		update_modified=False,
	)


def _http(status_code: int, payload: dict) -> dict:
	frappe.local.response["http_status_code"] = status_code
	return payload
