# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

from __future__ import annotations

import unittest

import frappe

from ltl_quote.api.carrier import _parse_enabled, list_quote_sources, set_carrier_enabled


def setUpModule():
	frappe.init(site="development.localhost", sites_path=".")
	frappe.connect()
	frappe.set_user("Administrator")


def tearDownModule():
	frappe.destroy()


class TestParseEnabled(unittest.TestCase):
	def test_enable_words(self):
		self.assertEqual(_parse_enabled(None, "enable"), 1)
		self.assertEqual(_parse_enabled("1", None), 1)
		self.assertEqual(_parse_enabled("on", None), 1)

	def test_disable_words(self):
		self.assertEqual(_parse_enabled(None, "disable"), 0)
		self.assertEqual(_parse_enabled("0", None), 0)
		self.assertEqual(_parse_enabled("off", None), 0)

	def test_read_when_omitted(self):
		self.assertIsNone(_parse_enabled(None, None))
		self.assertIsNone(_parse_enabled("", ""))

	def test_bad_action(self):
		with self.assertRaises(frappe.ValidationError):
			_parse_enabled(None, "pause")


class TestSetCarrierEnabled(unittest.TestCase):
	def test_disable_and_restore(self):
		if not frappe.db.exists("LTL Carrier", "DAYTON"):
			self.skipTest("DAYTON carrier is not installed")

		original = frappe.db.get_value("LTL Carrier", "DAYTON", "enabled")
		try:
			disabled = set_carrier_enabled(carrier="Dayton", action="disable")
			self.assertEqual(disabled["carrier"], "DAYTON")
			self.assertEqual(disabled["enabled"], 0)
			self.assertEqual(frappe.db.get_value("LTL Carrier", "DAYTON", "enabled"), 0)

			enabled = set_carrier_enabled(carrier="DAYTON", enabled=1)
			self.assertEqual(enabled["enabled"], 1)
			self.assertEqual(enabled["action"], "enable")

			current = set_carrier_enabled(carrier="DAYTON")
			self.assertEqual(current["enabled"], 1)
		finally:
			frappe.db.set_value("LTL Carrier", "DAYTON", "enabled", original, update_modified=False)
			frappe.db.commit()


class TestListQuoteSources(unittest.TestCase):
	def test_groups_enabled_and_disabled(self):
		if not frappe.db.exists("LTL Carrier", "DAYTON"):
			self.skipTest("DAYTON carrier is not installed")
		if not frappe.db.exists("LTL Carrier", "ESTES"):
			self.skipTest("ESTES carrier is not installed")

		payload = list_quote_sources()
		enabled_ids = {row["carrier"] for row in payload["enabled"]}
		disabled_ids = {row["carrier"] for row in payload["disabled"]}

		self.assertEqual(payload["status"], "success")
		self.assertIn("DAYTON", enabled_ids)
		self.assertNotIn("DAYTON", disabled_ids)
		self.assertIn("ESTES", disabled_ids)
		self.assertNotIn("ESTES", enabled_ids)
		self.assertEqual(payload["enabled_count"], len(payload["enabled"]))
		self.assertEqual(payload["disabled_count"], len(payload["disabled"]))
		self.assertEqual(payload["count"], payload["enabled_count"] + payload["disabled_count"])
		self.assertTrue(all(row["enabled"] == 1 for row in payload["enabled"]))
		self.assertTrue(all(row["enabled"] == 0 for row in payload["disabled"]))
		self.assertNotIn("api_key", payload["enabled"][0])
		self.assertNotIn("api_secret", payload["enabled"][0])

	def test_status_enabled_omits_disabled(self):
		if not frappe.db.exists("LTL Carrier", "ESTES"):
			self.skipTest("ESTES carrier is not installed")

		payload = list_quote_sources(status="enabled")
		self.assertIn("enabled", payload)
		self.assertNotIn("disabled", payload)
		self.assertTrue(all(row["enabled"] == 1 for row in payload["enabled"]))
		self.assertNotIn("ESTES", {row["carrier"] for row in payload["enabled"]})
		self.assertEqual(payload["count"], len(payload["enabled"]))
		self.assertGreater(payload["disabled_count"], 0)

	def test_bad_status(self):
		with self.assertRaises(frappe.ValidationError):
			list_quote_sources(status="pause")

	def test_desk_user_can_list(self):
		if not frappe.db.exists("User", "user1@gmail.com"):
			self.skipTest("user1@gmail.com is not installed")
		frappe.set_user("user1@gmail.com")
		try:
			payload = list_quote_sources()
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(payload["status"], "success")
		self.assertGreater(payload["count"], 0)
