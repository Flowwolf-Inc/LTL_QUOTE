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
	_seed_carrier_accessorials()
	_disable_mock_carriers()
	_migrate_quote_currency()
	frappe.db.commit()


def after_migrate():
	_ensure_dayton_carrier()
	_ensure_arcbest_carrier()
	_ensure_tforce_carrier()
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


def _seed_carrier_accessorials():
	"""Backfill accessorial mappings for real carriers so rating keeps working.

	The runtime rate path reads the per-carrier accessorial mapping table (no more
	hardcoded fallback), so existing DAYTON / ARCB / TFORCE records must be seeded.
	"""
	from ltl_quote.carrier_network.accessorial_sync import sync_carrier_accessorials

	for carrier_code in ("DAYTON", "ARCB", "TFORCE"):
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
