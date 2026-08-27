# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

import frappe

from ltl_quote.carrier_network.adapters.base import BaseCarrierAdapter
from ltl_quote.carrier_network.adapters.arcbest import ArcBestCarrierAdapter
from ltl_quote.carrier_network.adapters.dayton import DaytonCarrierAdapter
from ltl_quote.carrier_network.adapters.mock import MockCarrierAdapter
from ltl_quote.carrier_network.adapters.smc3 import SMC3CarrierAdapter
from ltl_quote.carrier_network.adapters.tforce import TForceCarrierAdapter

CONNECTOR_MAP: dict[str, type[BaseCarrierAdapter]] = {
	"Mock": MockCarrierAdapter,
	"Dayton": DaytonCarrierAdapter,
	"ArcBest API": ArcBestCarrierAdapter,
	"TForce": TForceCarrierAdapter,
	"SMC3": SMC3CarrierAdapter,
}


def get_adapter(carrier_doc) -> BaseCarrierAdapter:
	if not hasattr(carrier_doc, "get_password"):
		carrier_name = carrier_doc.get("name") if isinstance(carrier_doc, dict) else getattr(carrier_doc, "name", None)
		if carrier_name and frappe.db.exists("LTL Carrier", carrier_name):
			carrier_doc = frappe.get_doc("LTL Carrier", carrier_name)

	connector = (carrier_doc.connector_type or "Mock").strip()
	adapter_cls = CONNECTOR_MAP.get(connector)

	if not adapter_cls:
		frappe.log_error(
			message=(
				f"Unknown connector_type '{connector}' on LTL Carrier "
				f"{getattr(carrier_doc, 'name', '')}. "
				f"Valid keys: {', '.join(CONNECTOR_MAP)}"
			),
			title="LTL Carrier Registry Mismatch",
		)
		adapter_cls = MockCarrierAdapter

	return adapter_cls(carrier_doc)


def get_carrier_adapter(carrier_or_connector):
	"""FLOWWOLF registry alias — accepts a carrier document or connector type string."""
	if isinstance(carrier_or_connector, str):
		carrier_doc = frappe._dict(
			{
				"carrier_code": carrier_or_connector.upper(),
				"carrier_name": carrier_or_connector,
				"connector_type": carrier_or_connector,
			}
		)
		return get_adapter(carrier_doc)
	return get_adapter(carrier_or_connector)


def get_enabled_carriers() -> list:
	settings = frappe.get_single("LTL Platform Settings")
	if settings.use_mock_carriers:
		return _ensure_mock_carriers()

	carriers = frappe.get_all(
		"LTL Carrier",
		filters={"enabled": 1},
		pluck="name",
	)
	return [frappe.get_doc("LTL Carrier", name) for name in carriers]


def _ensure_mock_carriers() -> list:
	"""Seed and return mock carriers for development."""
	mock_carriers = [
		("XPO", "XPO Logistics", 92),
		("ODFL", "Old Dominion Freight Line", 94),
		("SAIA", "Saia LTL Freight", 88),
		("ESTES", "Estes Express Lines", 90),
		("FEDEX_FREIGHT", "FedEx Freight", 91),
		("UPS_FREIGHT", "UPS Freight", 93),
	]
	result = []
	for code, name, reliability in mock_carriers:
		if not frappe.db.exists("LTL Carrier", code):
			doc = frappe.get_doc(
				{
					"doctype": "LTL Carrier",
					"carrier_code": code,
					"carrier_name": name,
					"enabled": 1,
					"connector_type": "Mock",
					"reliability_score": reliability,
				}
			)
			doc.insert(ignore_permissions=True)
		result.append(frappe.get_doc("LTL Carrier", code))
	return result
