# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""SMC3 Credentials v2 — carrier vault requirements and save (Section 3.0)."""

from __future__ import annotations

import json
import re

import frappe
import requests

from ltl_quote.carrier_network.smc3_token import AUTH_USER_MESSAGE, SMC3AuthError, is_invalid_access_token
from ltl_quote.utils.transaction_log import log_carrier_transaction

DEFAULT_CREDENTIALS_BASE = "https://eva.smc3.com/credentials/v2/app"
AUTH_ERROR_CODE = "10000401"
CARRIER_ERROR_CODE = "10000079"

_SECURE_NAME_TOKENS = ("password", "secret", "apikey", "api_key", "api-key", "token")


@frappe.whitelist()
def get_carrier_requirements(scac, carrier=None):
	"""GET /credentials/v2/app/requirements/carrier/{SCAC}."""
	scac = _normalize_scac(scac)
	adapter = _adapter(carrier)
	url = f"{_credentials_base(adapter)}/requirements/carrier/{scac}"
	headers = _eva_headers(adapter, scac)
	data = _send(adapter, "GET", url, headers, scac, payload=None)
	_assert_message_status(data, default="SMC3 credential requirements request failed.")
	fields = parse_credential_requirements(data)
	status = _message_status(data)
	return {
		"status": "success",
		"ok": True,
		"scac": str(data.get("scac") or scac).upper(),
		"transaction_id": str(data.get("transactionId") or "").strip(),
		"fields": fields,
		"message": str(status.get("message") or "Credential requirements retrieved.").strip(),
		"message_status": status,
		"raw": data,
	}


@frappe.whitelist()
def save_carrier_credentials(scac, smc_attributes, carrier=None):
	"""POST /credentials/v2/app/credentials/carrier/{SCAC} with smcAttributes."""
	scac = _normalize_scac(scac)
	attributes = normalize_smc_attributes_payload(smc_attributes)
	if not attributes:
		frappe.throw("At least one credential attribute is required.")
	adapter = _adapter(carrier)
	url = f"{_credentials_base(adapter)}/credentials/carrier/{scac}"
	headers = _eva_headers(adapter, scac)
	body = {"smcAttributes": attributes}
	data = _send(adapter, "POST", url, headers, scac, payload=body)
	_assert_message_status(data, default="SMC3 credential save request failed.")
	status = _message_status(data)
	return {
		"status": "success",
		"ok": True,
		"scac": str(data.get("scac") or scac).upper(),
		"transaction_id": str(data.get("transactionId") or "").strip(),
		"message": str(status.get("message") or "Carrier credentials stored in the SMC3 vault.").strip(),
		"message_status": status,
		"raw": data,
	}


@frappe.whitelist()
def get_carrier_credentials(scac, carrier=None):
	"""GET /credentials/v2/app/credentials/carrier/{SCAC}."""
	scac = _normalize_scac(scac)
	adapter = _adapter(carrier)
	url = f"{_credentials_base(adapter)}/credentials/carrier/{scac}"
	headers = _eva_headers(adapter, scac)
	data = _send(adapter, "GET", url, headers, scac, payload=None)
	_assert_message_status(data, default="SMC3 credential lookup failed.")
	fields = mask_secure_credential_values(parse_credential_requirements(data))
	status = _message_status(data)
	return {
		"status": "success",
		"ok": True,
		"scac": str(data.get("scac") or scac).upper(),
		"transaction_id": str(data.get("transactionId") or "").strip(),
		"fields": fields,
		"message": str(status.get("message") or "Carrier credentials retrieved.").strip(),
		"message_status": status,
		"raw": _sanitize_payload_for_log(data) if isinstance(data, dict) else data,
	}


@frappe.whitelist()
def delete_carrier_credentials(scac, carrier=None):
	"""DELETE /credentials/v2/app/credentials/carrier/{SCAC}."""
	scac = _normalize_scac(scac)
	adapter = _adapter(carrier)
	url = f"{_credentials_base(adapter)}/credentials/carrier/{scac}"
	headers = _eva_headers(adapter, scac)
	data = _send(adapter, "DELETE", url, headers, scac, payload=None)
	_assert_message_status(data, default="SMC3 credential delete request failed.")
	status = _message_status(data)
	return {
		"status": "success",
		"ok": True,
		"scac": str(data.get("scac") or scac).upper(),
		"transaction_id": str(data.get("transactionId") or "").strip(),
		"message": str(status.get("message") or "Carrier credentials removed from the SMC3 vault.").strip(),
		"message_status": status,
		"raw": data,
	}


def parse_credential_requirements(data: dict | None) -> list[dict]:
	"""Normalize a Credentials v2 requirements payload into dialog field rows."""
	data = data if isinstance(data, dict) else {}
	raw = (
		data.get("smcAttributes")
		or data.get("attributes")
		or data.get("requirements")
		or data.get("credentialRequirements")
		or []
	)
	if isinstance(raw, dict):
		raw = raw.get("smcAttributes") or raw.get("attribute") or raw.get("fields") or [raw]
	if not isinstance(raw, list):
		raw = [raw] if raw else []
	rows = []
	seen = set()
	for item in raw:
		parsed = _parse_requirement_row(item)
		if not parsed:
			continue
		key = parsed["name"].lower()
		if key in seen:
			continue
		seen.add(key)
		rows.append(parsed)
	return rows


def normalize_smc_attributes_payload(raw) -> list[dict]:
	"""Accept a list, dict, or JSON string of name/value pairs for the vault POST."""
	value = raw
	if isinstance(value, str):
		text = value.strip()
		if not text:
			return []
		try:
			value = json.loads(text)
		except ValueError:
			frappe.throw("smc_attributes must be a list of name/value pairs.")
	if isinstance(value, dict):
		if isinstance(value.get("smcAttributes"), list):
			value = value.get("smcAttributes")
		else:
			value = [{"name": key, "value": item} for key, item in value.items()]
	if not isinstance(value, list):
		return []
	rows = []
	for item in value:
		if not isinstance(item, dict):
			continue
		name = str(item.get("name") or item.get("key") or item.get("attributeName") or "").strip()
		if not name:
			continue
		attr_value = item.get("value")
		if attr_value is None:
			attr_value = item.get("attributeValue") or ""
		rows.append({"name": name, "key": name, "value": "" if attr_value is None else str(attr_value)})
	return rows


def credentials_error_message(status: dict | None, default: str = "") -> str:
	"""User-facing Credentials v2 error, including 10000401 / 10000079."""
	status = status if isinstance(status, dict) else {}
	code = str(status.get("code") or "").strip()
	message = str(status.get("message") or "").strip()
	resolution = str(status.get("resolution") or "").strip()
	if code == AUTH_ERROR_CODE:
		message = message or AUTH_USER_MESSAGE
	elif code == CARRIER_ERROR_CODE:
		message = message or "SMC3 rejected the carrier credentials request."
	head = message or default or "SMC3 credentials request failed."
	if code:
		head = f"[{code}] {head}"
	parts = [head]
	if resolution and resolution not in head:
		parts.append(resolution)
	info = status.get("information")
	if isinstance(info, list):
		extra = " ".join(str(item).strip() for item in info if str(item or "").strip())
		if extra:
			parts.append(extra)
	elif isinstance(info, str) and info.strip():
		parts.append(info.strip())
	return " ".join(parts)


def _parse_requirement_row(item) -> dict | None:
	if isinstance(item, str):
		name = item.strip()
		if not name:
			return None
		secure = _is_secure_name(name)
		return {
			"name": name,
			"label": _label_from_name(name),
			"required": True,
			"secure": secure,
			"type": "password" if secure else "string",
			"description": "",
		}
	if not isinstance(item, dict):
		return None
	name = str(
		item.get("name")
		or item.get("key")
		or item.get("attributeName")
		or item.get("id")
		or item.get("field")
		or ""
	).strip()
	if not name:
		return None
	type_name = str(item.get("type") or item.get("dataType") or item.get("inputType") or "").strip().lower()
	secure = bool(item.get("secure") or item.get("masked") or item.get("sensitive")) or type_name in {
		"password",
		"secret",
	} or _is_secure_name(name)
	required = item.get("required")
	if required is None:
		required = item.get("isRequired")
	if required is None:
		required = True
	if isinstance(required, str):
		required = required.strip().lower() in {"1", "true", "yes", "y"}
	else:
		required = bool(required)
	label = str(
		item.get("label") or item.get("displayName") or item.get("description") or _label_from_name(name)
	).strip()
	value = item.get("value")
	if value is None:
		value = item.get("attributeValue")
	parsed = {
		"name": name,
		"label": label or _label_from_name(name),
		"required": required,
		"secure": secure,
		"type": type_name or ("password" if secure else "string"),
		"description": str(item.get("help") or item.get("hint") or item.get("description") or "").strip(),
	}
	if value is not None:
		parsed["value"] = str(value)
	return parsed


def mask_secure_credential_values(fields: list[dict] | None) -> list[dict]:
	"""Replace stored secret values before returning them to the desk / API."""
	out = []
	for row in fields or []:
		if not isinstance(row, dict):
			continue
		item = dict(row)
		if item.get("secure") and item.get("value") not in (None, ""):
			item["value"] = "***"
		out.append(item)
	return out


def _label_from_name(name: str) -> str:
	spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(name or ""))
	spaced = spaced.replace("_", " ").replace("-", " ")
	return " ".join(part.capitalize() if part.lower() != "api" else "API" for part in spaced.split()) or name


def _is_secure_name(name: str) -> bool:
	value = re.sub(r"[^a-z0-9]+", "", str(name or "").lower())
	return any(token.replace("_", "").replace("-", "") in value for token in _SECURE_NAME_TOKENS)


def _normalize_scac(scac) -> str:
	value = str(scac or "").strip().upper()
	if not value:
		frappe.throw("A carrier SCAC is required.")
	return value


def _carrier_doc(carrier=None):
	name = str(carrier or "").strip() or "SMC3"
	if not frappe.db.exists("LTL Carrier", name):
		if name != "SMC3" and frappe.db.exists("LTL Carrier", "SMC3"):
			name = "SMC3"
		else:
			frappe.throw("SMC3 carrier record not found.")
	doc = frappe.get_doc("LTL Carrier", name)
	frappe.has_permission("LTL Carrier", "write", doc=doc, throw=True)
	return doc


def _adapter(carrier=None):
	from ltl_quote.carrier_network.adapters.smc3 import SMC3CarrierAdapter

	return SMC3CarrierAdapter(_carrier_doc(carrier))


def _credentials_base(adapter) -> str:
	override = str((adapter._config or {}).get("credentials_base_url") or "").strip().rstrip("/")
	if override:
		return override
	return DEFAULT_CREDENTIALS_BASE


def _eva_headers(adapter, scac: str) -> dict:
	headers = adapter._eva_headers(scac)
	if not str(headers.get("X-Eva-Access-Id") or "").strip():
		frappe.throw(
			f"EVA Access ID is required to manage credentials for {scac}. "
			"Set it on the matching SMC3 network carrier row."
		)
	return headers


def _message_status(data) -> dict:
	status = data.get("messageStatus") if isinstance(data, dict) else {}
	return status if isinstance(status, dict) else {}


def _assert_message_status(data, default: str) -> None:
	status = _message_status(data)
	code = str(status.get("code") or "").strip()
	flag = str(status.get("status") or "").upper()
	if flag in {"", "PASS"} and code not in {AUTH_ERROR_CODE, CARRIER_ERROR_CODE}:
		return
	if flag == "PASS":
		return
	frappe.throw(credentials_error_message(status, default))


def _send(adapter, method: str, url: str, headers: dict, scac: str, payload) -> dict:
	method = str(method or "GET").upper()
	log_body = _sanitize_payload_for_log(payload)
	try:
		kwargs = {"headers": headers, "timeout": 60}
		if payload is not None and method not in {"GET", "DELETE", "HEAD"}:
			kwargs["json"] = payload
		response = adapter.token_service.request(method, url, **kwargs)
	except SMC3AuthError:
		frappe.throw(credentials_error_message({"code": AUTH_ERROR_CODE, "status": "FAIL"}, AUTH_USER_MESSAGE))
	except requests.exceptions.RequestException as exc:
		log_carrier_transaction(
			carrier="SMC3",
			method=method,
			url=url,
			origin=scac,
			dest="",
			headers=headers,
			request_body=log_body,
			response_text=str(exc),
			status="Connection Failed",
		)
		frappe.throw(f"SMC3 credentials connection error: {exc}")

	data = None
	if response.status_code == 204 or not (response.content or b"").strip():
		data = {}
	else:
		try:
			data = response.json() if response.content else {}
		except ValueError:
			data = None
	status = _message_status(data) if isinstance(data, dict) else {}
	flag = str(status.get("status") or "PASS").upper()
	ok = response.status_code in (200, 201, 204, 207) and flag in {"", "PASS"}
	log_payload = data if isinstance(data, dict) else (response.text or "")[:500]
	log_carrier_transaction(
		carrier="SMC3",
		method=method,
		url=url,
		origin=scac,
		dest="",
		headers=headers,
		request_body=log_body,
		response_text=_sanitize_payload_for_log(log_payload) if isinstance(log_payload, dict) else log_payload,
		status="Success" if ok else "API Error",
	)
	if is_invalid_access_token(response):
		frappe.throw(credentials_error_message({"code": AUTH_ERROR_CODE, "status": "FAIL"}, AUTH_USER_MESSAGE))
	if data is None:
		frappe.throw(f"SMC3 credentials returned non-JSON response: {(response.text or '')[:250]}")
	if not isinstance(data, dict):
		frappe.throw("SMC3 credentials returned an unexpected payload.")
	if response.status_code not in (200, 201, 204, 207):
		_assert_message_status(data, default="SMC3 credentials request failed.")
		frappe.throw(adapter._format_http_error(response) if hasattr(adapter, "_format_http_error") else f"SMC3 HTTP {response.status_code}")
	return data


def _sanitize_payload_for_log(payload):
	if not isinstance(payload, dict):
		return payload
	attrs = payload.get("smcAttributes")
	if not isinstance(attrs, list):
		return payload
	safe = []
	for item in attrs:
		if not isinstance(item, dict):
			safe.append(item)
			continue
		row = dict(item)
		name = str(row.get("name") or row.get("key") or "")
		if row.get("value") not in (None, "") and _is_secure_name(name):
			row["value"] = "***"
		safe.append(row)
	return {**payload, "smcAttributes": safe}
