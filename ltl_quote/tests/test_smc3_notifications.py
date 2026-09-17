# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

from __future__ import annotations

import unittest

from ltl_quote.api.flowwolf import _is_smc3_connector
from ltl_quote.api.smc3_notifications import parse_notification_callbacks


class TestSMC3Notifications(unittest.TestCase):
	def test_parse_callback_endpoints_list(self):
		rows = parse_notification_callbacks(
			{
				"callbackEndpoints": [
					{
						"id": "abc-123",
						"endpoint": "https://example.com/api/method/ltl_quote.api.webhooks.smc3_status_update",
						"effectiveDate": "20260901",
						"service": "STATUS",
						"transactionId": "txn-1",
					},
					{
						"callbackId": "def-456",
						"callbackUrl": "https://example.com/hook-2",
						"effective_date": "20260902",
					},
				]
			}
		)
		self.assertEqual(len(rows), 2)
		self.assertEqual(rows[0]["callback_id"], "abc-123")
		self.assertEqual(rows[0]["service"], "STATUS")
		self.assertEqual(rows[0]["transaction_id"], "txn-1")
		self.assertEqual(rows[1]["callback_id"], "def-456")
		self.assertTrue(rows[1]["endpoint"].startswith("https://"))

	def test_parse_single_callback_object(self):
		rows = parse_notification_callbacks(
			{
				"callbackEndpoint": {
					"id": "only-one",
					"url": "https://desk.example.com/hook",
					"service": "status",
				}
			}
		)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["callback_id"], "only-one")
		self.assertEqual(rows[0]["service"], "STATUS")

	def test_parse_document_callback_service(self):
		rows = parse_notification_callbacks(
			{
				"callbackEndpoints": [
					{
						"id": "doc-1",
						"endpoint": "https://example.com/api/method/ltl_quote.api.webhooks.smc3_document_update",
						"effectiveDate": "20260907",
						"service": "document",
					}
				]
			}
		)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["callback_id"], "doc-1")
		self.assertEqual(rows[0]["service"], "DOCUMENT")

	def test_parse_document_payload_types(self):
		from ltl_quote.api.webhooks import parse_document_payload
		from ltl_quote.api.smc3 import DOCUMENT_TYPES
		from ltl_quote.carrier_network.adapters.smc3 import DOCUMENT_TYPES as ADAPTER_TYPES

		parsed = parse_document_payload(
			{
				"scac": "CNWY",
				"documentType": "INV",
				"fileType": "PDF",
				"referenceNumbers": {"proNumber": "204380071201", "bol": "444555678"},
			}
		)
		self.assertEqual(parsed["document_type"], "INV")
		self.assertEqual(parsed["document_types"], ["INV"])
		self.assertEqual(parsed["file_type"], "PDF")
		self.assertEqual(parsed["pro"], "204380071201")
		self.assertEqual(parsed["bol"], "444555678")

		all_types = parse_document_payload(
			{"documentType": "ALL", "referenceNumbers": {"proNumber": "1"}}
		)
		self.assertIn("INV", all_types["document_types"])
		self.assertIn("WC", all_types["document_types"])
		self.assertNotIn("BL", all_types["document_types"])
		self.assertTrue({"INV", "WC"}.issubset(set(DOCUMENT_TYPES)))
		self.assertTrue({"INV", "WC"}.issubset(ADAPTER_TYPES))

	def test_flowwolf_smc3_connector_detection(self):
		self.assertTrue(_is_smc3_connector("SMC3"))
		self.assertTrue(_is_smc3_connector("", "SMC3"))
		self.assertFalse(_is_smc3_connector("Dayton"))
		self.assertFalse(_is_smc3_connector("", "DAYTON"))


def run_checks():
	suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestSMC3Notifications)
	result = unittest.TextTestRunner(verbosity=2).run(suite)
	if not result.wasSuccessful():
		raise AssertionError(f"failures={result.failures} errors={result.errors}")
	return {"ok": True, "tests": result.testsRun}
