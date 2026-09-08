# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""Map user-facing carrier_preference strings to LTL Carrier DocType names."""

from __future__ import annotations

import json
from typing import Any

import frappe

# User-facing aliases -> LTL Carrier.name (autoname = carrier_code)
CARRIER_DOC_IDS = {
	"DAYTON": "DAYTON",
	"ARCBEST": "ARCB",
	"ARCB": "ARCB",
	"ABF": "ARCB",
	"ABFS": "ARCB",
	"TFORCE": "TFORCE",
	"TFF": "TFORCE",
	"SMC3": "SMC3",
	"MOCK": "MOCK",
}


def resolve_carrier_id(raw_preference: str | None) -> str | None:
	"""
	Resolve a Postman/API carrier_preference string to an LTL Carrier DocName.

	Returns:
	    - "DAYTON", "ARCB", or "MOCK" when matched
	    - None when no preference (aggregate all enabled carriers)
	"""
	if not raw_preference:
		return None

	raw = str(raw_preference).strip()
	upper = raw.upper()

	if upper in CARRIER_DOC_IDS:
		return CARRIER_DOC_IDS[upper]

	if "DAYTON" in upper:
		return "DAYTON"

	if any(token in upper for token in ("TFORCE", "TFORCE FREIGHT", "TFF")):
		return "TFORCE"

	if any(token in upper for token in ("ARCBEST", "ARCB", "ABF", "ABFS")):
		return "ARCB"

	if "SMC3" in upper:
		return "SMC3"

	if "MOCK" in upper:
		return "MOCK"

	if frappe.db.exists("LTL Carrier", raw):
		return raw

	by_code = frappe.db.get_value("LTL Carrier", {"carrier_code": upper}, "name")
	if by_code:
		return by_code

	by_name = frappe.db.get_value("LTL Carrier", {"carrier_name": raw}, "name")
	if by_name:
		return by_name

	# Unrecognized explicit preference — route to mock carriers for safe dev testing
	return "MOCK"


def parse_carrier_tokens(raw_carriers) -> list[str]:
	"""Split a carriers param into cleaned, lower-cased tokens.

	Accepts a comma-separated string (``"SMC3,Dayton"``), a JSON array string,
	or a list (``["SMC3", "Dayton"]``). Empty/None input returns ``[]``.
	"""
	if raw_carriers is None:
		return []
	if isinstance(raw_carriers, (list, tuple, set)):
		tokens: list[str] = []
		for item in raw_carriers:
			tokens.extend(parse_carrier_tokens(item))
		return tokens

	text = str(raw_carriers).strip()
	if not text:
		return []
	if text.startswith("[") and text.endswith("]"):
		try:
			parsed = json.loads(text)
			if isinstance(parsed, list):
				return parse_carrier_tokens(parsed)
		except ValueError:
			pass
	return [part.strip().lower() for part in text.split(",") if part.strip()]


def _raw_carrier_tokens(raw_carriers) -> list[str]:
	"""Split a carriers param while preserving original token casing (for warnings)."""
	if raw_carriers is None:
		return []
	if isinstance(raw_carriers, (list, tuple, set)):
		tokens: list[str] = []
		for item in raw_carriers:
			tokens.extend(_raw_carrier_tokens(item))
		return tokens

	text = str(raw_carriers).strip()
	if not text:
		return []
	if text.startswith("[") and text.endswith("]"):
		try:
			parsed = json.loads(text)
			if isinstance(parsed, list):
				return _raw_carrier_tokens(parsed)
		except ValueError:
			pass
	return [part.strip() for part in text.split(",") if part.strip()]


def resolve_carrier_token(token: str) -> str | None:
	"""Normalize a filter alias to an LTL Carrier doc name.

	Same alias table as ``resolve_carrier_id``, but unknown tokens return ``None``
	instead of falling back to MOCK.
	"""
	if not token:
		return None

	raw = str(token).strip()
	if not raw:
		return None
	upper = raw.upper()

	if upper in CARRIER_DOC_IDS:
		return CARRIER_DOC_IDS[upper]

	compact = upper.replace(" ", "").replace("-", "").replace("_", "")
	if compact in {"DAYTON", "DAYTONFREIGHT", "DAYTONFREIGHTLINES", "DAFG"}:
		return "DAYTON"
	if compact in {"TFORCE", "TFORCEFREIGHT", "TFF", "TFFA"}:
		return "TFORCE"
	if compact in {"ARCBEST", "ARCBESTAPI", "ARCB", "ABF", "ABFS", "ABFFREIGHT"}:
		return "ARCB"
	if compact in {"SMC3"}:
		return "SMC3"
	if compact in {"MOCK"}:
		return "MOCK"

	return _lookup_carrier_doc_name(raw, upper)


def _lookup_carrier_doc_name(raw: str, upper: str) -> str | None:
	db = getattr(frappe, "db", None)
	if db is None:
		return None
	try:
		if db.exists("LTL Carrier", raw):
			return raw
		by_code = db.get_value("LTL Carrier", {"carrier_code": upper}, "name")
		if by_code:
			return by_code
		by_name = db.get_value("LTL Carrier", {"carrier_name": raw}, "name")
		if by_name:
			return by_name
	except Exception:
		return None
	return None


def get_enabled_carriers() -> list:
	"""Return enabled LTL Carrier docs (honors Platform Settings mock mode)."""
	from ltl_quote.carrier_network.registry import get_enabled_carriers as registry_enabled

	return registry_enabled()


def _carriers_from_mapping(source):
	if source is None:
		return None
	try:
		if "carriers" not in source:
			return None
		value = source.get("carriers")
	except TypeError:
		return None
	if value in (None, ""):
		return None
	return value


def _carriers_from_request_args():
	"""Query string (?carriers=) — Frappe omits this from form_dict on JSON POSTs."""
	req = getattr(frappe, "request", None)
	args = getattr(req, "args", None) if req is not None else None
	if args is None:
		return None
	getlist = getattr(args, "getlist", None)
	if callable(getlist):
		values = [v for v in getlist("carriers") if v not in (None, "")]
		if not values:
			return None
		return values[0] if len(values) == 1 else values
	return _carriers_from_mapping(args)


def extract_requested_carriers(
	request: dict | None = None,
	body: dict | None = None,
	kwargs: dict | None = None,
):
	"""Read ``carriers`` from payload, JSON body, query string, kwargs, or form_dict."""
	for source in (request, body, kwargs):
		value = _carriers_from_mapping(source)
		if value is not None:
			return value

	query_value = _carriers_from_request_args()
	if query_value is not None:
		return query_value

	form_dict = None
	try:
		form_dict = getattr(frappe, "form_dict", None)
	except Exception:
		form_dict = None
	if not form_dict:
		try:
			form_dict = getattr(getattr(frappe, "local", None), "form_dict", None)
		except Exception:
			form_dict = None
	return _carriers_from_mapping(form_dict)


def load_carriers_for_rating(requested=None, carrier_preference=None) -> tuple[list, list, list]:
	"""Intersect requested aliases with enabled LTL Carriers.

	Returns ``(resolved_carrier_docs, warnings_list, available_carrier_metadata)``.
	``carriers`` wins over ``carrier_preference`` when both are provided.
	Empty requested + empty preference returns all enabled carriers.
	Unknown or disabled tokens are skipped with a warning.
	"""
	enabled = get_enabled_carriers() or []
	available = [_carrier_metadata(doc) for doc in enabled]
	enabled_by_id = _index_enabled_carriers(enabled)

	raw_tokens = _raw_carrier_tokens(requested)
	if not raw_tokens:
		raw_tokens = _raw_carrier_tokens(carrier_preference)

	if not raw_tokens:
		return list(enabled), [], available

	resolved: list = []
	warnings: list[dict] = []
	seen: set[str] = set()
	for raw in raw_tokens:
		carrier_id = resolve_carrier_token(raw)
		doc = enabled_by_id.get((carrier_id or "").upper()) if carrier_id else None
		if not doc:
			warnings.append(
				{
					"carrier": raw,
					"error": f"Unknown or disabled carrier: {raw}",
				}
			)
			continue
		key = str(getattr(doc, "name", None) or carrier_id).upper()
		if key in seen:
			continue
		seen.add(key)
		resolved.append(doc)

	return resolved, warnings, available


def _carrier_metadata(doc) -> dict[str, str]:
	return {
		"id": str(getattr(doc, "name", None) or getattr(doc, "carrier_code", "") or ""),
		"name": str(getattr(doc, "carrier_name", None) or getattr(doc, "name", "") or ""),
		"connector_type": str(getattr(doc, "connector_type", "") or ""),
	}


def _index_enabled_carriers(enabled: list) -> dict[str, Any]:
	index: dict[str, Any] = {}
	for doc in enabled:
		for key in (
			getattr(doc, "name", None),
			getattr(doc, "carrier_code", None),
			getattr(doc, "carrier_name", None),
		):
			text = str(key or "").strip().upper()
			if text:
				index[text] = doc
	return index


def applied_filter_ids(carrier_docs: list) -> list[str]:
	ids: list[str] = []
	for doc in carrier_docs or []:
		name = str(getattr(doc, "name", None) or getattr(doc, "carrier_code", "") or "").strip()
		if name:
			ids.append(name)
	return ids


_CONNECTOR_FILTER_ALIASES = {
	"DAYTON": {"DAYTON", "DAYTON FREIGHT"},
	"TFORCE": {"TFORCE", "TFF", "TFORCE FREIGHT"},
	"ARCB": {"ARCB", "ARCBEST", "ABF", "ABFS", "ARCBEST API"},
	"SMC3": {"SMC3"},
	"MOCK": {"MOCK"},
}


def _filter_keys(carrier_docs: list) -> set[str]:
	keys: set[str] = set()
	for doc in carrier_docs or []:
		name = str(getattr(doc, "name", "") or "").upper()
		code = str(getattr(doc, "carrier_code", "") or "").upper()
		cname = str(getattr(doc, "carrier_name", "") or "").upper()
		connector = str(getattr(doc, "connector_type", "") or "").upper()
		for val in (name, code, cname, connector):
			if val:
				keys.add(val)
		for alias_id, aliases in _CONNECTOR_FILTER_ALIASES.items():
			if name in aliases or code in aliases or connector in aliases or alias_id in {name, code, connector}:
				keys.update(aliases)
		if connector == "ARCBEST API":
			keys.update(_CONNECTOR_FILTER_ALIASES["ARCB"])
	keys.discard("")
	return keys


def quote_matches_carrier_filter(quote, carrier_docs: list) -> bool:
	"""True when a ranked quote belongs to one of the resolved carrier docs."""
	if not carrier_docs:
		return False
	allowed = _filter_keys(carrier_docs)
	if isinstance(quote, dict):
		fields = [
			quote.get("carrier_code"),
			quote.get("carrier_name"),
			quote.get("carrier"),
			quote.get("source"),
			quote.get("rate_source"),
			quote.get("scac"),
		]
	else:
		fields = [
			getattr(quote, "carrier_code", None),
			getattr(quote, "carrier_name", None),
			getattr(quote, "carrier", None),
			getattr(quote, "source", None),
			getattr(quote, "rate_source", None),
			getattr(quote, "scac", None),
			getattr(quote, "quoted_scac", None),
		]
	for val in fields:
		text = str(val or "").strip().upper()
		if not text:
			continue
		if text in allowed:
			return True
		if "SMC3" in allowed and "SMC3" in text:
			return True
	return False


def apply_carrier_response_filter(
	quotes: list,
	errors: list,
	carrier_docs: list,
	filter_active: bool,
	warnings: list | None = None,
) -> tuple[list, list]:
	"""Optionally drop quotes/errors from non-requested carriers, then append warnings."""
	filtered_quotes = list(quotes or [])
	filtered_errors = list(errors or [])
	if filter_active:
		filtered_quotes = [q for q in filtered_quotes if quote_matches_carrier_filter(q, carrier_docs)]
		allowed = _filter_keys(carrier_docs)
		kept_errors: list = []
		for err in filtered_errors:
			if not isinstance(err, dict):
				kept_errors.append(err)
				continue
			carrier = str(err.get("carrier") or "").strip().upper()
			message = str(err.get("error") or "").lower()
			if "unknown or disabled carrier" in message:
				kept_errors.append(err)
				continue
			if not carrier or carrier in allowed or ("SMC3" in allowed and "SMC3" in carrier):
				kept_errors.append(err)
		filtered_errors = kept_errors
	if warnings:
		filtered_errors.extend(warnings)
	return filtered_quotes, filtered_errors


def load_carrier_for_rating(carrier_id: str | None) -> tuple[list, str]:
	"""
	Verify carrier configuration and return documents ready for the rate engine.

	Raises frappe.DoesNotExistError when a specific carrier ID is missing from the DB.
	"""
	if not carrier_id:
		from ltl_quote.carrier_network.registry import get_enabled_carriers

		return get_enabled_carriers(), "Multi-Carrier"

	if carrier_id == "MOCK":
		from ltl_quote.carrier_network.registry import _ensure_mock_carriers

		return _ensure_mock_carriers(), "Mock Carriers"

	if not frappe.db.exists("LTL Carrier", carrier_id):
		raise frappe.DoesNotExistError(f"LTL Carrier '{carrier_id}' not found")

	carrier_doc = frappe.get_doc("LTL Carrier", carrier_id)
	return [carrier_doc], carrier_doc.carrier_name


def log_carrier_label(carrier_id: str | None, carrier_doc_name: str | None = None) -> str:
	"""Map carrier ID to a value accepted by LTL Carrier Transaction Log.carrier_name."""
	if carrier_doc_name:
		name = carrier_doc_name.lower()
		if "dayton" in name:
			return "Dayton Freight"
		if "tforce" in name:
			return "TForce Freight"
		if "arc" in name or "abf" in name:
			return "ArcBest"
		if "smc3" in name:
			return "SMC3"

	if carrier_id == "DAYTON":
		return "Dayton Freight"
	if carrier_id == "TFORCE":
		return "TForce Freight"
	if carrier_id == "ARCB":
		return "ArcBest"
	if carrier_id == "SMC3":
		return "SMC3"
	if carrier_id == "MOCK":
		return "Mock Carriers"
	return "Multi-Carrier"
