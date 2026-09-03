# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""DSDC eBOL unit-of-measure mapping for Dayton / SMC3 handling units."""

import unittest
from unittest.mock import patch

from ltl_quote.api.payload import (
	digital_ltl_cube_unit,
	digital_ltl_dimension_unit,
	digital_ltl_handling_unit_type,
	digital_ltl_weight_unit,
)
from ltl_quote.carrier_network.adapters.dayton import (
	_build_dayton_handling_units,
	_sanitize_dayton_ebol_integers,
)
from ltl_quote.carrier_network.smc3_bol import _dimension_unit


class TestDigitalLtlUnits(unittest.TestCase):
	def test_dimension_unit_aliases(self):
		self.assertEqual(digital_ltl_dimension_unit("IN"), "Inches")
		self.assertEqual(digital_ltl_dimension_unit("in"), "Inches")
		self.assertEqual(digital_ltl_dimension_unit("INCHES"), "Inches")
		self.assertEqual(digital_ltl_dimension_unit(""), "Inches")
		self.assertEqual(digital_ltl_dimension_unit("CM"), "Centimeters")
		self.assertEqual(digital_ltl_dimension_unit("centimeters"), "Centimeters")
		self.assertEqual(_dimension_unit("IN"), "Inches")

	def test_weight_and_cube_units(self):
		self.assertEqual(digital_ltl_weight_unit("LBS"), "Pounds")
		self.assertEqual(digital_ltl_weight_unit("LB"), "Pounds")
		self.assertEqual(digital_ltl_weight_unit("KG"), "Kilograms")
		self.assertEqual(digital_ltl_cube_unit("FT"), "Feet")
		self.assertEqual(digital_ltl_cube_unit("M"), "Meters")

	def test_handling_unit_type(self):
		self.assertEqual(digital_ltl_handling_unit_type("PALLET"), "PAT")
		self.assertEqual(digital_ltl_handling_unit_type("PLT"), "PAT")
		self.assertEqual(digital_ltl_handling_unit_type("SKID"), "SKD")
		self.assertEqual(digital_ltl_handling_unit_type("SKD"), "SKD")
		self.assertEqual(digital_ltl_handling_unit_type(""), "PAT")

	@patch(
		"ltl_quote.carrier_network.adapters.dayton._resolve_dayton_packaging_type",
		return_value="SKID",
	)
	def test_dayton_handling_units_use_dsdc_enums(self, _mock_packaging):
		handling_units, _weight, _pieces = _build_dayton_handling_units(
			items=[
				{
					"description": "Freight",
					"freight_class": "70",
					"quantity": 1,
					"weight": 1000,
					"length": 48,
					"width": 40,
					"height": 48,
					"dimension_units": "IN",
				}
			],
			fallback_weight=1000,
			fallback_class="70",
			fallback_pieces=1,
			fallback_dimension_unit="IN",
			hu_type="PALLET",
		)
		hu = handling_units[0]
		self.assertEqual(hu["dimensionsUnit"], "Inches")
		self.assertEqual(hu["weightUnit"], "Pounds")
		self.assertEqual(hu["type"], "PAT")
		self.assertNotEqual(hu["dimensionsUnit"], "IN")

	@patch(
		"ltl_quote.carrier_network.adapters.dayton._resolve_dayton_packaging_type",
		return_value="SKID",
	)
	def test_dayton_handling_units_centimeters(self, _mock_packaging):
		handling_units, _, _ = _build_dayton_handling_units(
			items=[
				{
					"description": "Freight",
					"freight_class": "70",
					"quantity": 1,
					"weight": 500,
					"dimension_unit": "CM",
				}
			],
			fallback_weight=500,
			fallback_class="70",
			fallback_pieces=1,
			hu_type="SKID",
		)
		self.assertEqual(handling_units[0]["dimensionsUnit"], "Centimeters")
		self.assertEqual(handling_units[0]["type"], "SKD")

	def test_sanitize_rewrites_short_uoms(self):
		payload = _sanitize_dayton_ebol_integers(
			{
				"shipmentTotals": {
					"grossWeight": 1000.0,
					"weightUnit": "LBS",
					"dimensionsUnit": "IN",
					"cubeDimensionsUnit": "FT",
					"linearLength": 0,
				},
				"commodities": {
					"handlingUnits": [
						{
							"count": 1,
							"type": "PALLET",
							"weight": 1000.0,
							"weightUnit": "LBS",
							"dimensionsUnit": "IN",
							"length": 48,
							"width": 40,
							"height": 48,
							"lineItems": [{"weight": 1000.0, "pieces": 1, "weightUnit": "LBS"}],
						}
					]
				},
			}
		)
		hu = payload["commodities"]["handlingUnits"][0]
		self.assertEqual(hu["dimensionsUnit"], "Inches")
		self.assertEqual(hu["weightUnit"], "Pounds")
		self.assertEqual(hu["type"], "PAT")
		self.assertEqual(payload["shipmentTotals"]["dimensionsUnit"], "Inches")
		self.assertEqual(payload["shipmentTotals"]["weightUnit"], "Pounds")
		self.assertEqual(payload["shipmentTotals"]["cubeDimensionsUnit"], "Feet")


class TestRequiredPartyFields(unittest.TestCase):
	def test_bol_payload_keeps_real_party_fields(self):
		from ltl_quote.carrier_network.smc3_bol import build_bol_payload

		payload = build_bol_payload(FAILED_TXN_QUOTE, is_test=True, account="12345")
		self.assertEqual(payload["origin"]["name"], "Main Warehouse Dispatch")
		self.assertEqual(payload["origin"]["contact"]["name"], "Alex Rivera")
		self.assertEqual(payload["destination"]["contact"]["name"], "Jordan Lee")
		self.assertNotIn("John Doe", str(payload))
		self.assertNotIn("12 S. Main", str(payload))

	def test_bol_payload_requires_shipper_contact_name(self):
		from ltl_quote.carrier_network.smc3_bol import build_bol_payload

		quote = dict(FAILED_TXN_QUOTE)
		quote.pop("contact_name")
		with self.assertRaises(Exception) as ctx:
			build_bol_payload(quote, is_test=False, account="12345")
		self.assertIn("Shipper Contact Name", str(ctx.exception))

	def test_bol_production_requires_account(self):
		from ltl_quote.carrier_network.smc3_bol import build_bol_payload

		with self.assertRaises(Exception) as ctx:
			build_bol_payload(FAILED_TXN_QUOTE, is_test=False, account="")
		self.assertIn("account number", str(ctx.exception).lower())

	def test_bol_sandbox_allows_default_account(self):
		from ltl_quote.carrier_network.smc3_bol import DEFAULT_SANDBOX_ACCOUNT, build_bol_payload

		payload = build_bol_payload(FAILED_TXN_QUOTE, is_test=True, account="")
		self.assertEqual(payload["origin"]["account"], DEFAULT_SANDBOX_ACCOUNT)

	def test_dispatch_payload_requires_shipper_email(self):
		from types import SimpleNamespace

		from ltl_quote.carrier_network.smc3_dispatch import build_dispatch_payload

		quote = dict(FAILED_TXN_QUOTE)
		quote.pop("origin_contact_email")
		with self.assertRaises(Exception) as ctx:
			build_dispatch_payload(SimpleNamespace(pickup_date="2026-09-08"), quote)
		self.assertIn("Shipper Contact Email", str(ctx.exception))

	def test_dispatch_payload_uses_quote_contacts(self):
		from types import SimpleNamespace

		from ltl_quote.carrier_network.smc3_dispatch import build_dispatch_payload

		payload = build_dispatch_payload(SimpleNamespace(pickup_date="2026-09-08"), FAILED_TXN_QUOTE)
		self.assertEqual(payload["origin"]["contact"]["name"], "Alex Rivera")
		self.assertEqual(payload["origin"]["contact"]["email"], "alex@warehouse.example")
		self.assertEqual(payload["destination"]["contact"]["name"], "Jordan Lee")
		self.assertNotIn("Jane Doe", str(payload))
		self.assertNotIn("shipperContactPerson@email.com", str(payload))


def run_checks():
	"""Run this module's unit tests via `bench execute` (no site allow_tests flag)."""
	loader = unittest.defaultTestLoader
	suite = unittest.TestSuite()
	suite.addTests(loader.loadTestsFromTestCase(TestDigitalLtlUnits))
	suite.addTests(loader.loadTestsFromTestCase(TestRequiredPartyFields))
	result = unittest.TextTestRunner(verbosity=2).run(suite)
	if not result.wasSuccessful():
		raise AssertionError(f"failures={result.failures} errors={result.errors}")
	return {"ok": True, "tests": result.testsRun}


FAILED_TXN_QUOTE = {
	"origin_zip": "60601",
	"origin_city": "Chicago",
	"origin_state": "IL",
	"destination_zip": "75201",
	"destination_city": "Dallas",
	"destination_state": "TX",
	"total_weight": 1000,
	"freight_class": "60",
	"pieces": 2,
	"dimension_uom": "IN",
	"commodity_description": "materials",
	"shipper_name": "Main Warehouse Dispatch",
	"shipper_address": "123 Logistics Way",
	"consignee_name": "Destination Receiver",
	"consignee_address": "456 Customer Ave",
	"contact_name": "Alex Rivera",
	"contact_phone": "3125550199",
	"origin_contact_email": "alex@warehouse.example",
	"destination_contact_name": "Jordan Lee",
	"destination_contact_phone": "2145550188",
	"destination_contact_email": "jordan@receiver.example",
	"items": [
		{
			"description": "materials",
			"weight": 1000,
			"quantity": 2,
			"freight_class": "60",
			"length": 20,
			"width": 20,
			"height": 20,
			"dimension_units": "IN",
			"packaging_units": "SKD",
			"units": "BOX",
		}
	],
}


def dry_run_failed_smc3_payload():
	"""Rebuild LTL-TXN-2026-00378 and assert SMC3 no longer sends dimensionsUnit=IN."""
	from ltl_quote.carrier_network.smc3_bol import build_bol_payload

	payload = build_bol_payload(FAILED_TXN_QUOTE, is_test=True, account="12345")
	hu = (payload.get("commodities") or {}).get("handlingUnits") or []
	unit = hu[0].get("dimensionsUnit") if hu else None
	if unit != "Inches":
		raise AssertionError(f"expected dimensionsUnit Inches, got {unit!r} in {hu}")
	if unit == "IN":
		raise AssertionError("SMC3 payload still has short-code IN")
	return {
		"ok": True,
		"dimensionsUnit": unit,
		"handling_unit_type": hu[0].get("type"),
		"weightUnit": hu[0].get("weightUnit"),
	}


def dry_run_smc3_sandbox_post():
	"""POST the rebuilt payload to SMC3 sandbox BOL (isTest) and return validator status."""
	from ltl_quote.carrier_network.adapters.smc3 import SMC3CarrierAdapter
	from ltl_quote.carrier_network.smc3_bol import build_bol_payload

	built = dry_run_failed_smc3_payload()
	adapter = SMC3CarrierAdapter()
	quote = dict(FAILED_TXN_QUOTE)
	quote["is_test"] = True
	quote["scac"] = "SMCA"
	try:
		data = adapter._create_bill_of_lading("SMCA", quote, True)
	except Exception as exc:
		text = str(exc)
		return {
			"built_dimensionsUnit": built["dimensionsUnit"],
			"smc3_status": "THROWN",
			"smc3_message": text,
			"ok": "Invalid handling unit dimensions unit" not in text,
		}
	status = data.get("messageStatus") if isinstance(data.get("messageStatus"), dict) else {}
	return {
		"built_dimensionsUnit": built["dimensionsUnit"],
		"smc3_status": status.get("status"),
		"smc3_code": status.get("code"),
		"smc3_message": status.get("message"),
		"ok": str(status.get("status") or "").upper() == "PASS"
		or str(status.get("code") or "") != "10000329",
	}
