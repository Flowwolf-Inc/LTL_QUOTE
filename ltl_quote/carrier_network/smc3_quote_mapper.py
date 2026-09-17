# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""SMC3 Aggregate Pricing request builder and UI quote transformer."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from frappe.utils import cint, flt, getdate

from ltl_quote.carrier_network.smc3_onboarded import (
	carrier_display_name,
	is_demo_display_name,
	is_sandbox_scac,
)

RATE_SOURCE = "SMC3"


def build_carrier_entry(
	scac: str,
	eva_access_id: str,
	bill_to: dict,
	payment: dict,
) -> dict:
	return {
		"scac": str(scac or "").strip().upper(),
		"evaAccessId": str(eva_access_id or "").strip(),
		"billTo": bill_to,
		"payment": payment,
	}


def build_aggregate_payload(
	*,
	origin: dict,
	destination: dict,
	payment_terms: str,
	commodities: list[dict],
	carriers: list[dict],
	pricing_types: list[str] | None = None,
	service_levels: list[str] | None = None,
	pickup_date: str | None = None,
	unit_of_measure: dict | None = None,
	accessorial_codes: list[str] | None = None,
	iso_pickup_date: bool = True,
) -> dict:
	"""Build POST /pricing/v3/app/aggregate request body."""
	terms = str(payment_terms or "Prepaid").strip() or "Prepaid"
	payload: dict[str, Any] = {
		"carriers": carriers,
		"pricingTypes": _normalize_pricing_types(pricing_types),
		"serviceLevels": _normalize_service_levels(service_levels),
		"transit": {"pickupDate": pickup_date or _default_pickup_date(iso_pickup_date)},
		"unitOfMeasure": unit_of_measure or {"weight": "Pounds", "dimensions": "Inches"},
		"commodities": commodities,
		"origin": origin,
		"destination": destination,
	}
	if accessorial_codes:
		payload["accessorials"] = {"codes": accessorial_codes}
	# Payment on each carrier entry is the source of truth; keep a top-level
	# copy so specs that read shipment-level terms still see Prepaid/Collect.
	if carriers and isinstance(carriers[0].get("payment"), dict):
		payload["payment"] = dict(carriers[0]["payment"])
	elif terms:
		payload["payment"] = {"terms": terms, "payer": "Shipper"}
	return payload


def transform_carrier_results(
	response: dict | None,
	label_overrides: dict[str, str] | None = None,
	requested_scacs: list[str] | None = None,
	is_sandbox: bool = False,
) -> list[dict]:
	"""Parse carrierResults[] into standardized UI quote objects.

	Schema:
	    carrier_name, source, scac, total_charge, transit_days, service_level
	"""
	quotes: list[dict] = []
	seen: set[str] = set()
	overrides = label_overrides or {}

	for result in _extract_result_rows(response):
		mapped = transform_carrier_result(result, overrides)
		if not mapped:
			continue
		unique_id = mapped["carrier_quote_id"]
		if unique_id in seen:
			continue
		seen.add(unique_id)
		quotes.append(mapped)
	return apply_requested_carrier_names(quotes, requested_scacs, overrides, is_sandbox=is_sandbox)


def transform_carrier_result(result: dict | None, label_overrides: dict[str, str] | None = None) -> dict | None:
	if not isinstance(result, dict):
		return None
	if not _is_passing(result):
		return None

	info = result.get("shipmentInfo") or result.get("shipment") or {}
	if not isinstance(info, dict):
		info = {}
	total = flt(
		info.get("totalCharge")
		or result.get("totalCharge")
		or (result.get("pricing") or {}).get("totalCharge")
		or 0
	)
	if total <= 0:
		return None

	scac = _extract_scac(result)
	if not scac:
		return None

	service = result.get("service") if isinstance(result.get("service"), dict) else {}
	service_level = str(service.get("level") or service.get("description") or "Standard").strip() or "Standard"
	pricing_type = str(result.get("pricingType") or result.get("pricing_type") or "").strip()
	quote_id = str(((result.get("quote") or {}) if isinstance(result.get("quote"), dict) else {}).get("quoteId") or "").strip()
	unique_id = "|".join(part for part in (scac, pricing_type, service.get("level") or service_level, quote_id) if part)

	transit_block = result.get("transit") if isinstance(result.get("transit"), dict) else {}
	transit = transit_block.get("movementInfo") if isinstance(transit_block.get("movementInfo"), dict) else transit_block
	transit_days = cint(transit.get("estimatedTransitDays") or result.get("estimatedTransitDays") or 0)
	details = info.get("totalChargeDetails") if isinstance(info.get("totalChargeDetails"), dict) else {}
	overrides = label_overrides or {}
	carrier_name = carrier_display_name(scac, overrides.get(scac), _extract_api_name(result))
	if carrier_name.upper() in {"SMC3", "SMC"} or is_demo_display_name(carrier_name):
		if is_sandbox_scac(scac):
			carrier_name = "SMC3 Demo Carrier"
		else:
			return None

	accessorials = result.get("accessorials") or []
	breakdown: dict[str, float] = {}
	for acc in accessorials:
		if not isinstance(acc, dict):
			continue
		code = str(acc.get("code") or acc.get("description") or "").strip()
		amount = flt(acc.get("chargeAmount") or 0)
		if code and amount:
			breakdown[code] = breakdown.get(code, 0) + amount

	return {
		"carrier_name": carrier_name,
		"source": RATE_SOURCE,
		"scac": scac,
		"total_charge": total,
		"transit_days": transit_days,
		"service_level": service_level,
		"carrier_quote_id": unique_id,
		"pricing_type": pricing_type,
		"quote_id": quote_id,
		"currency": str(info.get("currency") or result.get("currency") or "USD") or "USD",
		"linehaul_charge": flt(details.get("lineHaulNetCharge") or details.get("lineHaulGrossCharge") or 0),
		"fuel_surcharge": flt(details.get("fuelSurcharge") or 0),
		"accessorial_charge": flt(details.get("accessorialsTotal") or 0),
		"accessorial_breakdown": breakdown,
		"estimated_delivery_date": _parse_pickup_date(transit.get("estimatedDeliveryDate")),
		"raw_response": result,
	}


def apply_requested_carrier_names(
	quotes: list[dict],
	requested_scacs: list[str] | None,
	label_overrides: dict[str, str] | None = None,
	is_sandbox: bool = False,
) -> list[dict]:
	"""Prefer real PASS SCACs. In sandbox only, attach varied SMCA stand-in rates to requested carriers."""
	overrides = label_overrides or {}
	real = [
		quote
		for quote in quotes
		if quote.get("scac")
		and not is_sandbox_scac(quote.get("scac"))
		and not is_demo_display_name(quote.get("carrier_name"))
	]
	if real:
		return _collapse_quotes_by_scac(real)
	if not is_sandbox:
		return []

	requested = []
	seen: set[str] = set()
	for scac in requested_scacs or []:
		code = str(scac or "").strip().upper()
		if not code or is_sandbox_scac(code) or code in seen:
			continue
		seen.add(code)
		requested.append({"scac": code, "carrier_label": overrides.get(code)})
	standin = _sandbox_standin_quote(quotes)
	if not standin or not requested:
		return []

	remapped: list[dict] = []
	for row in requested:
		scac = str(row["scac"]).upper()
		name = carrier_display_name(scac, row.get("carrier_label"))
		if not name or is_demo_display_name(name) or name.upper() in {"SMC3", "SMC"}:
			continue
		cloned = dict(standin)
		cloned["scac"] = scac
		cloned["carrier_name"] = name
		cloned["source"] = RATE_SOURCE
		cloned["carrier_quote_id"] = "|".join(
			part for part in (scac, cloned.get("pricing_type"), cloned.get("service_level"), "sandbox-standin") if part
		)
		apply_sandbox_mock_pricing(cloned, scac, standin)
		raw = dict(cloned.get("raw_response") or {})
		raw["_requested_scac"] = scac
		cloned["raw_response"] = raw
		remapped.append(cloned)
	return remapped


def apply_sandbox_mock_pricing(quote: dict, scac: str, base_quote: dict | None = None) -> dict:
	"""Deterministic per-SCAC mock totals for sandbox UI. Never call in production."""
	source = base_quote or quote
	base_rate = flt(source.get("total_charge") or 370.20)
	scac_hash = _scac_hash(scac)
	total = round(base_rate + (scac_hash % 50), 2)
	transit_days = 1 + (scac_hash % 4)
	old_total = flt(source.get("total_charge") or 0) or base_rate
	scale = (total / old_total) if old_total else 1.0
	linehaul = round(flt(source.get("linehaul_charge") or 0) * scale, 2)
	fuel = round(flt(source.get("fuel_surcharge") or 0) * scale, 2)
	accessorial = round(max(total - linehaul - fuel, 0.0), 2)
	quote["total_charge"] = total
	quote["transit_days"] = transit_days
	quote["linehaul_charge"] = linehaul
	quote["fuel_surcharge"] = fuel
	quote["accessorial_charge"] = accessorial
	return quote


def _scac_hash(scac: str) -> int:
	digest = hashlib.md5(str(scac or "").strip().upper().encode("utf-8")).hexdigest()
	return int(digest[:8], 16)


def _collapse_quotes_by_scac(quotes: list[dict]) -> list[dict]:
	best: dict[str, dict] = {}
	for quote in quotes:
		key = str(quote.get("scac") or "").strip().upper()
		if not key:
			continue
		current = best.get(key)
		if not current or flt(quote.get("total_charge") or 0) < flt(current.get("total_charge") or 0):
			best[key] = quote
	return list(best.values())


def _sandbox_standin_quote(quotes: list[dict]) -> dict | None:
	demo = [
		quote
		for quote in quotes
		if is_sandbox_scac(quote.get("scac")) or is_demo_display_name(quote.get("carrier_name"))
	]
	if not demo:
		return None
	stnd = [
		quote
		for quote in demo
		if str(quote.get("service_level") or "").upper() in {"STND", "STANDARD", "STANDARD LTL"}
	]
	pool = stnd or demo
	return min(pool, key=lambda quote: flt(quote.get("total_charge") or 0))


def _normalize_pricing_types(values: list[str] | None) -> list[str]:
	items = [str(value).strip() for value in (values or []) if str(value).strip()]
	if not items or any(item.lower() == "all" for item in items):
		return ["Contract", "Dynamic"]
	return items


def _normalize_service_levels(values: list[str] | None) -> list[str]:
	items = [str(value).strip() for value in (values or []) if str(value).strip()]
	if not items or any(item.lower() == "all" for item in items):
		return ["STND"]
	return items


def _extract_result_rows(response) -> list[dict]:
	if isinstance(response, list):
		return [row for row in response if isinstance(row, dict)]
	if not isinstance(response, dict):
		return []
	for key in ("carrierResults", "results", "quotes"):
		rows = response.get(key)
		if isinstance(rows, list):
			return [row for row in rows if isinstance(row, dict)]
	nested = response.get("data")
	if isinstance(nested, dict):
		return _extract_result_rows(nested)
	if isinstance(nested, list):
		return [row for row in nested if isinstance(row, dict)]
	return []


def _extract_scac(result: dict) -> str:
	carrier = result.get("carrier") if isinstance(result.get("carrier"), dict) else {}
	for value in (
		result.get("scac"),
		result.get("carrierScac"),
		result.get("carrierCode"),
		result.get("carrier_code"),
		carrier.get("scac"),
		carrier.get("carrierScac"),
		carrier.get("code"),
	):
		code = str(value or "").strip().upper()
		if code and code not in {"SMC3", "SMC"}:
			return code
		if code == "SMCA":
			return "SMCA"
	# Sandbox demo carrier is SMCA, not the connector name.
	if str(result.get("scac") or "").strip().upper() in {"SMCA"}:
		return "SMCA"
	return ""


def _extract_api_name(result: dict) -> str:
	carrier = result.get("carrier") if isinstance(result.get("carrier"), dict) else {}
	for value in (
		carrier.get("name"),
		carrier.get("carrierName"),
		carrier.get("displayName"),
		result.get("carrierName"),
		result.get("carrier_name"),
	):
		label = str(value or "").strip()
		if label:
			return label
	return ""


def _is_passing(result: dict) -> bool:
	status = ""
	message = result.get("messageStatus")
	if isinstance(message, dict):
		status = message.get("status") or ""
	elif isinstance(result.get("carrier"), dict):
		nested = result["carrier"].get("messageStatus")
		if isinstance(nested, dict):
			status = nested.get("status") or ""
	if not status:
		status = result.get("status") or ""
	return str(status).upper() == "PASS"


def _default_pickup_date(iso: bool) -> str:
	pickup = getdate()
	raw = pickup.strftime("%Y-%m-%d") if pickup else datetime.utcnow().strftime("%Y-%m-%d")
	if iso:
		return raw
	return raw.replace("-", "")


def _parse_pickup_date(value) -> str | None:
	raw = str(value or "").strip()
	if len(raw) == 8 and raw.isdigit():
		return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
	if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
		return raw[:10]
	return None
