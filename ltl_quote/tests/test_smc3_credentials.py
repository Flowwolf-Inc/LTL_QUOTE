# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

from __future__ import annotations

import unittest

from ltl_quote.api.smc3_credentials import (
	AUTH_ERROR_CODE,
	CARRIER_ERROR_CODE,
	credentials_error_message,
	mask_secure_credential_values,
	normalize_smc_attributes_payload,
	parse_credential_requirements,
)


class TestSMC3Credentials(unittest.TestCase):
	def test_parse_requirements_from_smc_attributes(self):
		fields = parse_credential_requirements(
			{
				"smcAttributes": [
					{"name": "apiKey", "description": "Carrier API Key", "required": True, "secure": True},
					{"name": "accountNumber", "label": "Account Number", "required": True},
					{"name": "username", "label": "Username", "required": False},
					{"name": "password", "type": "password", "required": True},
				]
			}
		)
		names = [row["name"] for row in fields]
		self.assertEqual(names, ["apiKey", "accountNumber", "username", "password"])
		self.assertTrue(fields[0]["secure"])
		self.assertEqual(fields[0]["label"], "Carrier API Key")
		self.assertTrue(fields[3]["secure"])
		self.assertFalse(fields[2]["required"])

	def test_normalize_attributes_accepts_json_and_dict(self):
		self.assertEqual(
			normalize_smc_attributes_payload('[{"name":"apiKey","value":"abc"}]'),
			[{"name": "apiKey", "key": "apiKey", "value": "abc"}],
		)
		self.assertEqual(
			normalize_smc_attributes_payload({"username": "desk", "password": "secret"}),
			[
				{"name": "username", "key": "username", "value": "desk"},
				{"name": "password", "key": "password", "value": "secret"},
			],
		)

	def test_auth_and_carrier_error_codes(self):
		auth = credentials_error_message(
			{"status": "FAIL", "code": AUTH_ERROR_CODE, "message": "Invalid EVA access id."}
		)
		self.assertIn("10000401", auth)
		self.assertIn("Invalid EVA access id.", auth)
		carrier = credentials_error_message(
			{"status": "FAIL", "code": CARRIER_ERROR_CODE, "message": "Carrier rejected credentials."}
		)
		self.assertIn("10000079", carrier)
		self.assertIn("Carrier rejected credentials.", carrier)

	def test_parse_stored_credentials_includes_values(self):
		fields = parse_credential_requirements(
			{
				"smcAttributes": [
					{"name": "username", "value": "desk"},
					{"name": "password", "value": "super-secret", "secure": True},
					{"name": "apiKey", "attributeValue": "live-key"},
				]
			}
		)
		by_name = {row["name"]: row for row in fields}
		self.assertEqual(by_name["username"]["value"], "desk")
		self.assertEqual(by_name["password"]["value"], "super-secret")
		self.assertTrue(by_name["password"]["secure"])
		self.assertEqual(by_name["apiKey"]["value"], "live-key")
		masked = mask_secure_credential_values(fields)
		masked_by_name = {row["name"]: row for row in masked}
		self.assertEqual(masked_by_name["username"]["value"], "desk")
		self.assertEqual(masked_by_name["password"]["value"], "***")
		self.assertEqual(masked_by_name["apiKey"]["value"], "***")


def run_checks():
	suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestSMC3Credentials)
	result = unittest.TextTestRunner(verbosity=2).run(suite)
	if not result.wasSuccessful():
		raise AssertionError(f"failures={result.failures} errors={result.errors}")
	return {"ok": True, "tests": result.testsRun}
