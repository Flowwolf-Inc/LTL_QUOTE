# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

import frappe

ACCESSORIALS = [
	("LIFTGATE", "Liftgate Service", "Flat", 75),
	("RESIDENTIAL", "Residential Delivery", "Flat", 65),
	("APPOINTMENT", "Delivery Appointment", "Flat", 45),
	("INSIDE_DELIVERY", "Inside Delivery", "Flat", 85),
	("HAZMAT", "Hazmat Handling", "Flat", 120),
	("LIMITED_ACCESS", "Limited Access", "Flat", 55),
]


def after_install():
	_fix_workspace_module()
	_ensure_platform_settings()
	_seed_accessorials()
	_ensure_dayton_carrier()
	_ensure_arcbest_carrier()
	_ensure_tforce_carrier()
	_ensure_smc3_carrier()
	_seed_carrier_accessorials()
	_disable_mock_carriers()
	_migrate_quote_currency()
	frappe.db.commit()


def after_migrate():
	_ensure_dayton_carrier()
	_ensure_arcbest_carrier()
	_ensure_tforce_carrier()
	_ensure_smc3_carrier()
	_seed_carrier_accessorials()
	_migrate_quote_currency()
	frappe.db.commit()


def _fix_workspace_module():
	"""Ensure LTL Quote workspace uses the Freight module (not legacy LTL Quote module name)."""
	if frappe.db.exists("Workspace", "LTL Quote"):
		frappe.db.set_value("Workspace", "LTL Quote", "module", "Freight", update_modified=False)


def _seed_accessorials():
	for code, name, charge_type, amount in ACCESSORIALS:
		if frappe.db.exists("LTL Accessorial", code):
			continue
		frappe.get_doc(
			{
				"doctype": "LTL Accessorial",
				"accessorial_code": code,
				"accessorial_name": name,
				"charge_type": charge_type,
				"default_amount": amount,
				"currency": "USD",
			}
		).insert(ignore_permissions=True)


def _ensure_platform_settings():
	if not frappe.db.exists("LTL Platform Settings", "LTL Platform Settings"):
		frappe.get_doc({"doctype": "LTL Platform Settings", "quote_currency": "USD"}).insert(
			ignore_permissions=True
		)
	else:
		frappe.db.set_value("LTL Platform Settings", "LTL Platform Settings", "quote_currency", "USD")


def _ensure_dayton_carrier():
	"""Seed Dayton Freight carrier record for production API integration."""
	carrier_data = {
		"doctype": "LTL Carrier",
		"carrier_code": "DAYTON",
		"carrier_name": "Dayton Freight Lines",
		"enabled": 1,
		"connector_type": "Dayton",
		"reliability_score": 90,
		"account_number": "0055666",
		"api_base_url": "https://api.daytonfreight.com",
		"auth_type": "Basic",
		"scac": "DAFG",
	}

	if frappe.db.exists("LTL Carrier", "DAYTON"):
		frappe.db.set_value(
			"LTL Carrier",
			"DAYTON",
			{
				"carrier_name": carrier_data["carrier_name"],
				"enabled": 1,
				"connector_type": "Dayton",
				"account_number": carrier_data["account_number"],
				"api_base_url": carrier_data["api_base_url"],
				"auth_type": "Basic",
			},
			update_modified=False,
		)
	else:
		frappe.get_doc(carrier_data).insert(ignore_permissions=True)


def _ensure_arcbest_carrier():
	"""Seed ArcBest (ABF Freight) carrier record for production XML rate API."""
	carrier_data = {
		"doctype": "LTL Carrier",
		"carrier_code": "ARCB",
		"carrier_name": "ArcBest Freight",
		"enabled": 1,
		"connector_type": "ArcBest API",
		"reliability_score": 91,
		"api_base_url": "https://www.abfs.com/xml/aquotexml.asp",
		"auth_type": "API Key",
		"scac": "ABFS",
	}

	if frappe.db.exists("LTL Carrier", "ARCB"):
		frappe.db.set_value(
			"LTL Carrier",
			"ARCB",
			{
				"carrier_name": carrier_data["carrier_name"],
				"enabled": 1,
				"connector_type": "ArcBest API",
				"api_base_url": carrier_data["api_base_url"],
				"auth_type": "API Key",
			},
			update_modified=False,
		)
	else:
		frappe.get_doc(carrier_data).insert(ignore_permissions=True)


def _ensure_tforce_carrier():
	"""Seed TForce Freight carrier record for OAuth rating + BOL APIs."""
	carrier_data = {
		"doctype": "LTL Carrier",
		"carrier_code": "TFORCE",
		"carrier_name": "TForce Freight",
		"enabled": 1,
		"connector_type": "TForce",
		"reliability_score": 90,
		"api_base_url": "https://api.tforcefreight.com",
		"api_version": "cie-v1",
		"auth_type": "OAuth2",
		"scac": "TFFA",
		"notes": (
			'{"serviceCode":"308","billingCode":"30",'
			'"token_url":"https://login.microsoftonline.com/'
			'ca4f5969-c10f-40d4-8127-e74b691f95de/oauth2/v2.0/token",'
			'"scope":"https://tffproduction.onmicrosoft.com/'
			'f06cb173-a8e6-44ad-89a1-06c1070a1f62/.default"}'
		),
	}

	if frappe.db.exists("LTL Carrier", "TFORCE"):
		frappe.db.set_value(
			"LTL Carrier",
			"TFORCE",
			{
				"carrier_name": carrier_data["carrier_name"],
				"enabled": 1,
				"connector_type": "TForce",
				"api_base_url": carrier_data["api_base_url"],
				"api_version": carrier_data["api_version"],
				"auth_type": "OAuth2",
				"scac": carrier_data["scac"],
			},
			update_modified=False,
		)
	else:
		frappe.get_doc(carrier_data).insert(ignore_permissions=True)


def _ensure_smc3_carrier():
	"""Seed SMC3 Pricing Aggregate connector. Do not overwrite stored credentials."""
	from ltl_quote.carrier_network.adapters.smc3 import DEFAULT_ENDPOINT, LEGACY_V1_ENDPOINT
	from ltl_quote.carrier_network.smc3_onboarded import DEFAULT_ENABLED_SCACS

	notes = (
		'{"minor_version":"1.2","willing_to_wait_seconds":30,"demo_instructions":"PASS",'
		'"pricing_types":["Contract","Dynamic"],"service_levels":["All"],'
		'"eva_access_id":"SANDBOX-TEST-01",'
		'"payment":{"terms":"Prepaid","payer":"Shipper"}}'
	)
	if frappe.db.exists("LTL Carrier", "SMC3"):
		doc = frappe.get_doc("LTL Carrier", "SMC3")
		doc.carrier_name = "SMC3"
		doc.enabled = 1
		doc.connector_type = "SMC3"
		current_url = (doc.api_base_url or "").strip().rstrip("/")
		legacy = LEGACY_V1_ENDPOINT.rstrip("/")
		if not current_url or current_url in {legacy, f"{legacy}/"}:
			doc.api_base_url = DEFAULT_ENDPOINT
		doc.api_version = doc.api_version or "v3"
		doc.auth_type = doc.auth_type or "API Key"
		if not (doc.notes or "").strip():
			doc.notes = notes
		_seed_smc3_network_carriers(doc)
		doc.save(ignore_permissions=True)
		return

	doc = frappe.get_doc(
		{
			"doctype": "LTL Carrier",
			"carrier_code": "SMC3",
			"carrier_name": "SMC3",
			"enabled": 1,
			"connector_type": "SMC3",
			"reliability_score": 80,
			"api_base_url": DEFAULT_ENDPOINT,
			"api_version": "v3",
			"auth_type": "API Key",
			"notes": notes,
		}
	)
	_seed_smc3_network_carriers(doc, enabled_scacs=DEFAULT_ENABLED_SCACS)
	doc.insert(ignore_permissions=True)


def _seed_smc3_network_carriers(doc, enabled_scacs=None):
	from ltl_quote.carrier_network.smc3_onboarded import (
		DEFAULT_ENABLED_SCACS,
		EVA_ONBOARDED_CARRIERS,
	)

	preferred_labels = {
		"ODFL": "Old Dominion Freight Line",
		"SAIA": "Saia LTL Freight",
		"EXLA": "Estes Express Lines",
		"DAFG": "Dayton Freight Lines",
		"ABFS": "ABF Freight",
		"PYLE": "A. Duie Pyle",
		"SMCA": "SMC3 Demo Carrier",
	}

	existing = {
		str(getattr(row, "scac", "") or "").strip().upper(): row
		for row in (doc.get("smc3_network_carriers") or [])
	}
	on_by_default = enabled_scacs or DEFAULT_ENABLED_SCACS
	default_eva = "SANDBOX-TEST-01"
	for carrier in EVA_ONBOARDED_CARRIERS:
		scac = carrier["scac"]
		if scac in existing:
			row = existing[scac]
			matrix_name = preferred_labels.get(scac) or carrier["name"]
			current_label = str(getattr(row, "carrier_label", "") or "").strip()
			if scac in preferred_labels or not current_label or current_label == scac:
				row.carrier_label = matrix_name
			if getattr(row, "contract_pricing", None) in (None, ""):
				row.contract_pricing = 1 if carrier["contract"] else 0
			if getattr(row, "dynamic_pricing", None) in (None, ""):
				row.dynamic_pricing = 1 if carrier["dynamic"] else 0
			continue
		doc.append(
			"smc3_network_carriers",
			{
				"scac": scac,
				"carrier_label": carrier["name"],
				"eva_access_id": default_eva,
				"account": "1234567890" if scac == "SMCA" else "",
				"enabled": 1 if scac in on_by_default else 0,
				"contract_pricing": 1 if carrier["contract"] else 0,
				"dynamic_pricing": 1 if carrier["dynamic"] else 0,
			},
		)


def _seed_carrier_accessorials():
	"""Backfill accessorial mappings for real carriers so rating keeps working.

	The runtime rate path reads the per-carrier accessorial mapping table (no more
	hardcoded fallback), so existing DAYTON / ARCB / TFORCE records must be seeded.
	"""
	from ltl_quote.carrier_network.accessorial_sync import sync_carrier_accessorials

	for carrier_code in ("DAYTON", "ARCB", "TFORCE", "SMC3"):
		if not frappe.db.exists("LTL Carrier", carrier_code):
			continue
		carrier_doc = frappe.get_doc("LTL Carrier", carrier_code)
		result = sync_carrier_accessorials(carrier_doc, seed_only=True)
		if result.get("added"):
			carrier_doc.save(ignore_permissions=True)


def _disable_mock_carriers():
	frappe.db.set_value(
		"LTL Platform Settings",
		"LTL Platform Settings",
		"use_mock_carriers",
		0,
		update_modified=False,
	)


def _migrate_quote_currency():
	"""Use USD for all LTL quote amounts (override site default INR display)."""
	frappe.db.set_value("LTL Platform Settings", "LTL Platform Settings", "quote_currency", "USD")
	frappe.db.sql(
		"""
		UPDATE `tabLTL Carrier Quote Line`
		SET currency = 'USD'
		WHERE currency IS NULL OR currency != 'USD'
		"""
	)
	frappe.db.sql(
		"""
		UPDATE `tabLTL Shipment`
		SET currency = 'USD'
		WHERE currency IS NULL OR currency != 'USD'
		"""
	)
	frappe.db.sql(
		"""
		UPDATE `tabLTL Accessorial`
		SET currency = 'USD'
		WHERE currency IS NULL OR currency != 'USD'
		"""
	)
