# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

from __future__ import annotations

import unittest

import frappe
from frappe.utils import cint

from ltl_quote.api.orgs import (
	carriers_for_org,
	ensure_org_settings,
	get_org_settings,
	org_display_name,
	set_org_accessorial_preference,
	set_org_quote_source_enabled,
)
from ltl_quote.api.quote import _create_quote_request
from ltl_quote.api.user_settings import SERVICE_GROUPS, STANDARD_ACCESSORIALS

QUOTE_SOURCE_FIELDS = ("carrier", "carrier_name", "enabled")
ACCESSORIAL_FIELDS = (
	"accessorial",
	"accessorial_code",
	"accessorial_name",
	"service_group",
	"show_on_form",
	"default_selected",
)
QUOTE_SOURCE_API_FIELDS = (
	"name",
	"carrier_code",
	"carrier_name",
	"scac",
	"connector_type",
	"reliability_score",
	"enabled",
)
ACCESSORIAL_API_FIELDS = (
	"accessorial",
	"code",
	"label",
	"master_name",
	"service_group",
	"show_on_form",
	"default_selected",
)


def setUpModule():
	frappe.init(site="development.localhost", sites_path=".")
	frappe.connect()
	frappe.set_user("Administrator")


def tearDownModule():
	frappe.destroy()


class TestOrgNames(unittest.TestCase):
	def test_known_orgs(self):
		self.assertEqual(org_display_name("ENVOY"), "Envoy")
		self.assertEqual(org_display_name("amerilux"), "Amerilux")
		self.assertEqual(org_display_name("Envoy"), "Envoy")

	def test_unknown_org_is_blank(self):
		self.assertEqual(org_display_name("DAYTON"), "")
		self.assertEqual(org_display_name(""), "")
		self.assertEqual(org_display_name(None), "")


class TestQuoteRequestOrg(unittest.TestCase):
	def _request(self, tenant_code=None):
		payload = {
			"origin_zip": "45414",
			"destination_zip": "60601",
			"total_weight": 100,
			"freight_class": "70",
			"save_request": False,
			"items": [],
		}
		if tenant_code is not None:
			payload["tenant_code"] = tenant_code
		return _create_quote_request(payload)

	def test_envoy_tenant_is_stored(self):
		self.assertEqual(self._request("ENVOY").org, "Envoy")

	def test_amerilux_tenant_is_stored(self):
		self.assertEqual(self._request("AMERILUX").org, "Amerilux")

	def test_blank_org_when_tenant_is_missing(self):
		self.assertEqual(self._request().org, "")


class TestOrgSettingsFields(unittest.TestCase):
	def test_parent_fields_for_both_orgs(self):
		for code, name in (("ENVOY", "Envoy"), ("AMERILUX", "Amerilux")):
			doc = ensure_org_settings(code)
			self.assertEqual(doc.org_code, code)
			self.assertEqual(doc.name, code)
			self.assertEqual(doc.org_name, name)
			self.assertTrue(doc.quote_sources)
			self.assertTrue(doc.accessorials)

	def test_quote_source_fields(self):
		for code in ("ENVOY", "AMERILUX"):
			doc = ensure_org_settings(code)
			payload = get_org_settings(code)
			self.assertEqual(payload["org_code"], code)
			self.assertEqual(payload["org_name"], doc.org_name)
			by_carrier = {row.carrier: row for row in doc.quote_sources}
			self.assertEqual(len(payload["quote_sources"]), len(by_carrier))
			for row in doc.quote_sources:
				for field in QUOTE_SOURCE_FIELDS:
					self.assertTrue(hasattr(row, field), field)
				self.assertTrue(row.carrier)
				self.assertTrue(row.carrier_name)
				self.assertIn(cint(row.enabled), (0, 1))
			for item in payload["quote_sources"]:
				for field in QUOTE_SOURCE_API_FIELDS:
					self.assertIn(field, item)
				stored = by_carrier[item["name"]]
				self.assertEqual(item["carrier_name"], stored.carrier_name)
				self.assertEqual(item["enabled"], cint(stored.enabled))
				self.assertIsInstance(item["scac"], str)
				self.assertIsInstance(item["connector_type"], str)

	def test_accessorial_fields(self):
		expected = {
			(code, group) for group, entries in STANDARD_ACCESSORIALS.items() for code, _ in entries
		}
		for org in ("ENVOY", "AMERILUX"):
			doc = ensure_org_settings(org)
			stored = {(row.accessorial_code, row.service_group) for row in doc.accessorials}
			self.assertTrue(expected.issubset(stored))
			for row in doc.accessorials:
				for field in ACCESSORIAL_FIELDS:
					self.assertTrue(getattr(row, field) not in (None, "") or field in ("default_selected", "show_on_form"))
				self.assertIn(row.service_group, SERVICE_GROUPS)
				self.assertIn(cint(row.show_on_form), (0, 1))
				self.assertIn(cint(row.default_selected), (0, 1))
				if not cint(row.show_on_form):
					self.assertEqual(cint(row.default_selected), 0)
			groups = get_org_settings(org)["accessorials"]
			self.assertEqual(set(groups), set(SERVICE_GROUPS))
			for group, rows in groups.items():
				for item in rows:
					for field in ACCESSORIAL_API_FIELDS:
						self.assertIn(field, item)
					self.assertEqual(item["service_group"], group)
					self.assertTrue(item["code"])
					self.assertTrue(item["label"])
					self.assertIn(cint(item["show_on_form"]), (0, 1))
					self.assertIn(cint(item["default_selected"]), (0, 1))

	def test_quote_source_toggle_keeps_other_org(self):
		if not frappe.db.exists("LTL Carrier", "DAYTON"):
			self.skipTest("DAYTON carrier is not installed")
		envoy = ensure_org_settings("ENVOY")
		amerilux = ensure_org_settings("AMERILUX")
		envoy_row = next(row for row in envoy.quote_sources if row.carrier == "DAYTON")
		amerilux_row = next(row for row in amerilux.quote_sources if row.carrier == "DAYTON")
		original = cint(envoy_row.enabled)
		other = cint(amerilux_row.enabled)
		try:
			saved = set_org_quote_source_enabled(org_code="ENVOY", carrier="DAYTON", enabled=0)
			self.assertEqual(saved["org_code"], "ENVOY")
			self.assertEqual(saved["carrier"], "DAYTON")
			self.assertEqual(saved["enabled"], 0)
			reloaded = ensure_org_settings("ENVOY")
			row = next(item for item in reloaded.quote_sources if item.carrier == "DAYTON")
			self.assertEqual(row.carrier_name, envoy_row.carrier_name)
			self.assertEqual(cint(row.enabled), 0)
			self.assertNotIn("DAYTON", {doc.name for doc in carriers_for_org("ENVOY")})
			untouched = ensure_org_settings("AMERILUX")
			other_row = next(item for item in untouched.quote_sources if item.carrier == "DAYTON")
			self.assertEqual(cint(other_row.enabled), other)
		finally:
			set_org_quote_source_enabled(org_code="ENVOY", carrier="DAYTON", enabled=original)

	def test_accessorial_toggle_round_trip(self):
		doc = ensure_org_settings("ENVOY")
		row = next(
			item
			for item in doc.accessorials
			if item.accessorial_code == "LIFTGATE" and item.service_group == "pickup"
		)
		original_show = cint(row.show_on_form)
		original_selected = cint(row.default_selected)
		try:
			saved = set_org_accessorial_preference(
				org_code="ENVOY",
				accessorial_code="LIFTGATE",
				service_group="pickup",
				show_on_form=1,
				default_selected=1,
			)
			self.assertEqual(saved["org_code"], "ENVOY")
			self.assertEqual(saved["code"], "LIFTGATE")
			self.assertEqual(saved["service_group"], "pickup")
			self.assertEqual(saved["show_on_form"], 1)
			self.assertEqual(saved["default_selected"], 1)
			reloaded = ensure_org_settings("ENVOY")
			updated = next(
				item
				for item in reloaded.accessorials
				if item.accessorial_code == "LIFTGATE" and item.service_group == "pickup"
			)
			self.assertEqual(updated.accessorial, row.accessorial)
			self.assertTrue(updated.accessorial_name)
			self.assertEqual(cint(updated.show_on_form), 1)
			self.assertEqual(cint(updated.default_selected), 1)
		finally:
			set_org_accessorial_preference(
				org_code="ENVOY",
				accessorial_code="LIFTGATE",
				service_group="pickup",
				show_on_form=original_show,
				default_selected=original_selected,
			)


class TestOrgSettingsPermission(unittest.TestCase):
	def test_non_administrator_is_rejected(self):
		if not frappe.db.exists("User", "user1@gmail.com"):
			self.skipTest("user1@gmail.com is not installed")
		frappe.set_user("user1@gmail.com")
		try:
			with self.assertRaises(frappe.PermissionError):
				get_org_settings(org_code="ENVOY")
			with self.assertRaises(frappe.PermissionError):
				set_org_quote_source_enabled(org_code="ENVOY", carrier="DAYTON", enabled=0)
			with self.assertRaises(frappe.PermissionError):
				set_org_accessorial_preference(
					org_code="AMERILUX",
					accessorial_code="LIFTGATE",
					service_group="pickup",
					show_on_form=0,
					default_selected=0,
				)
		finally:
			frappe.set_user("Administrator")
