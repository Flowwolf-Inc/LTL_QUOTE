# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from ltl_quote.api.quote import _fetch_quotes, resolve_request_from
from ltl_quote.api.revenova import (
	V1_METHOD_PATH,
	_extract_customer_id,
	_payload_body,
	handle_inbound_ltl_quote,
	rewrite_revenova_v1_path,
)
from ltl_quote.freight.doctype.ltl_integration_settings.ltl_integration_settings import (
	_validate_target_doctype,
	get_customer_mapping,
)


class TestResolveRequestFrom(unittest.TestCase):
	def test_administrator_user(self):
		with patch("ltl_quote.api.quote.frappe.get_roles", return_value=["System Manager"]):
			self.assertEqual(resolve_request_from("Administrator"), ["administrator"])

	def test_administrator_role(self):
		with patch("ltl_quote.api.quote.frappe.get_roles", return_value=["Administrator", "System Manager"]):
			self.assertEqual(resolve_request_from("jane@example.com"), ["administrator"])

	def test_system_manager_without_product_role(self):
		with patch("ltl_quote.api.quote.frappe.get_roles", return_value=["System Manager"]):
			self.assertEqual(resolve_request_from("ops@ltlquote.com"), ["administrator"])

	def test_shipper_even_with_system_manager(self):
		with patch("ltl_quote.api.quote.frappe.get_roles", return_value=["Shipper", "System Manager"]):
			self.assertEqual(resolve_request_from("shipper@ltlquote.local"), ["shipper"])

	def test_broker(self):
		with patch("ltl_quote.api.quote.frappe.get_roles", return_value=["Broker"]):
			self.assertEqual(resolve_request_from("broker@ltlquote.local"), ["broker"])

	def test_fallback_email(self):
		with patch("ltl_quote.api.quote.frappe.get_roles", return_value=["Employee"]):
			self.assertEqual(resolve_request_from("demo@gmail.com"), ["demo@gmail.com"])

	def test_guest(self):
		self.assertEqual(resolve_request_from("Guest"), ["Guest"])


class TestFetchQuotesWrapper(unittest.TestCase):
	def test_stamps_request_from_on_rate_payload(self):
		rates = {"status": "success", "data": {"quotes": []}}
		with (
			patch("ltl_quote.api.quote.get_ltl_rates", return_value=rates),
			patch("ltl_quote.api.quote.resolve_request_from", return_value=["shipper"]),
		):
			result = _fetch_quotes(payload={"origin_zip": "45414"})
		self.assertEqual(result["status"], "success")
		self.assertEqual(result["request from"], ["shipper"])
		self.assertEqual(result["data"]["quotes"], [])

	def test_wraps_non_dict_rates(self):
		with (
			patch("ltl_quote.api.quote.get_ltl_rates", return_value=[]),
			patch("ltl_quote.api.quote.resolve_request_from", return_value=["broker"]),
		):
			result = _fetch_quotes(payload={"origin_zip": "45414"})
		self.assertEqual(result["data"], [])
		self.assertEqual(result["request from"], ["broker"])


class TestExtractCustomerId(unittest.TestCase):
	def test_prefers_customer_over_account(self):
		self.assertEqual(_extract_customer_id({"customer": "ENVOY", "account": "other"}), "ENVOY")

	def test_tenant_code_fallback(self):
		self.assertEqual(_extract_customer_id({"tenant_code": "AMERILUX"}), "AMERILUX")

	def test_missing(self):
		self.assertEqual(_extract_customer_id({}), "")


class TestPayloadBody(unittest.TestCase):
	def test_unwraps_nested_payload(self):
		with patch(
			"ltl_quote.api.revenova._merge_request",
			return_value={"payload": {"customer": "ENVOY", "origin_zip": "45414"}},
		):
			body = _payload_body(None, {})
		self.assertEqual(body["customer"], "ENVOY")
		self.assertEqual(body["origin_zip"], "45414")


class TestInboundLtlQuote(unittest.TestCase):
	def setUp(self):
		self.auth = patch("ltl_quote.api.revenova._authenticate_webhook")
		self.auth.start()
		self.addCleanup(self.auth.stop)

		def _merge(payload, kwargs):
			return dict(payload) if isinstance(payload, dict) else {}

		self.merge = patch("ltl_quote.api.revenova._merge_request", side_effect=_merge)
		self.merge.start()
		self.addCleanup(self.merge.stop)
		self.local_ns = SimpleNamespace(response={}, flags=SimpleNamespace(in_test=True))
		self.local_patch = patch("ltl_quote.api.revenova.frappe.local", self.local_ns)
		self.local_patch.start()
		self.addCleanup(self.local_patch.stop)

	def test_missing_customer(self):
		result = handle_inbound_ltl_quote(payload={"origin_zip": "45414"})
		self.assertEqual(result["status"], "error")
		self.assertEqual(self.local_ns.response["http_status_code"], 400)

	def test_no_mapping(self):
		with (
			patch("ltl_quote.api.revenova.inbound_routing_enabled", return_value=True),
			patch("ltl_quote.api.revenova.get_customer_mapping", return_value=None),
		):
			result = handle_inbound_ltl_quote(payload={"customer": "UNKNOWN"})
		self.assertEqual(result["status"], "error")
		self.assertIn("No mapping exists for customer UNKNOWN", result["message"])
		self.assertEqual(self.local_ns.response["http_status_code"], 400)

	def test_disabled_mapping(self):
		mapping = {"customer": "ENVOY", "target_doctype": "Carrier Quote", "disabled": 1}
		with (
			patch("ltl_quote.api.revenova.inbound_routing_enabled", return_value=True),
			patch("ltl_quote.api.revenova.get_customer_mapping", return_value=mapping),
		):
			result = handle_inbound_ltl_quote(payload={"customer": "ENVOY"})
		self.assertEqual(result["status"], "rejected")
		self.assertEqual(self.local_ns.response["http_status_code"], 403)

	def test_inbound_routing_disabled(self):
		with patch("ltl_quote.api.revenova.inbound_routing_enabled", return_value=False):
			result = handle_inbound_ltl_quote(payload={"customer": "ENVOY"})
		self.assertEqual(result["status"], "rejected")
		self.assertEqual(self.local_ns.response["http_status_code"], 403)

	def test_carrier_quote_insert(self):
		mapping = {"customer": "ENVOY", "target_doctype": "Carrier Quote", "disabled": 0}
		with (
			patch("ltl_quote.api.revenova.inbound_routing_enabled", return_value=True),
			patch("ltl_quote.api.revenova.get_customer_mapping", return_value=mapping),
			patch(
				"ltl_quote.api.revenova._insert_mapped_doc",
				return_value={"name": "CQ-2026-00001", "action": "created"},
			) as insert,
		):
			result = handle_inbound_ltl_quote(
				payload={
					"customer": "ENVOY",
					"revenova_id": "a0B000000000001",
					"origin_zip": "45414",
				}
			)
		insert.assert_called_once()
		self.assertEqual(result["status"], "success")
		self.assertEqual(result["doctype"], "Carrier Quote")
		self.assertEqual(result["name"], "CQ-2026-00001")
		self.assertEqual(result["action"], "created")

	def test_unknown_doctype(self):
		mapping = {"customer": "ENVOY", "target_doctype": "Missing Doc", "disabled": 0}
		with (
			patch("ltl_quote.api.revenova.inbound_routing_enabled", return_value=True),
			patch("ltl_quote.api.revenova.get_customer_mapping", return_value=mapping),
			patch(
				"ltl_quote.api.revenova._insert_mapped_doc",
				side_effect=frappe.ValidationError("DocType Missing Doc does not exist."),
			),
			patch("ltl_quote.api.revenova.frappe.db", SimpleNamespace(rollback=lambda: None)),
		):
			result = handle_inbound_ltl_quote(payload={"customer": "ENVOY"})
		self.assertEqual(result["status"], "error")
		self.assertEqual(self.local_ns.response["http_status_code"], 400)


class TestPathRewrite(unittest.TestCase):
	def test_rewrites_v1_path(self):
		request = SimpleNamespace(path="/api/v1/revenova/ltl_quote", environ={"PATH_INFO": "/api/v1/revenova/ltl_quote"})
		with patch("ltl_quote.api.revenova.frappe.request", request):
			rewrite_revenova_v1_path()
		self.assertEqual(request.environ["PATH_INFO"], V1_METHOD_PATH)

	def test_moves_bearer_off_authorization(self):
		request = SimpleNamespace(
			path="/api/v1/revenova/ltl_quote",
			environ={
				"PATH_INFO": "/api/v1/revenova/ltl_quote",
				"HTTP_AUTHORIZATION": "Bearer secret-token",
			},
		)
		with patch("ltl_quote.api.revenova.frappe.request", request):
			rewrite_revenova_v1_path()
		self.assertEqual(request.environ["PATH_INFO"], V1_METHOD_PATH)
		self.assertEqual(request.environ.get("HTTP_X_REVENOVA_TOKEN"), "secret-token")
		self.assertNotIn("HTTP_AUTHORIZATION", request.environ)

	def test_ignores_other_paths(self):
		request = SimpleNamespace(
			path="/api/method/ping",
			environ={"PATH_INFO": "/api/method/ping", "HTTP_AUTHORIZATION": "Bearer keep-me"},
		)
		with patch("ltl_quote.api.revenova.frappe.request", request):
			rewrite_revenova_v1_path()
		self.assertEqual(request.environ["PATH_INFO"], "/api/method/ping")
		self.assertEqual(request.environ["HTTP_AUTHORIZATION"], "Bearer keep-me")


class TestCustomerMappingLookup(unittest.TestCase):
	def test_case_insensitive_match(self):
		settings = SimpleNamespace(
			customer_mappings=[
				SimpleNamespace(customer="envoy", target_doctype="Carrier Quote", disabled=0),
			]
		)
		with patch(
			"ltl_quote.freight.doctype.ltl_integration_settings.ltl_integration_settings.frappe.get_single",
			return_value=settings,
		):
			row = get_customer_mapping("ENVOY")
		self.assertEqual(row["target_doctype"], "Carrier Quote")
		self.assertEqual(row["disabled"], 0)

	def test_missing_row(self):
		settings = SimpleNamespace(customer_mappings=[])
		with patch(
			"ltl_quote.freight.doctype.ltl_integration_settings.ltl_integration_settings.frappe.get_single",
			return_value=settings,
		):
			self.assertIsNone(get_customer_mapping("ENVOY"))


class TestTargetDocTypeValidation(unittest.TestCase):
	def test_rejects_child_table(self):
		meta = SimpleNamespace(istable=1, issingle=0)
		fake_db = SimpleNamespace(exists=lambda *args, **kwargs: True)
		with (
			patch(
				"ltl_quote.freight.doctype.ltl_integration_settings.ltl_integration_settings.frappe.db",
				fake_db,
			),
			patch(
				"ltl_quote.freight.doctype.ltl_integration_settings.ltl_integration_settings.frappe.get_meta",
				return_value=meta,
			),
			self.assertRaises(frappe.ValidationError),
		):
			_validate_target_doctype("LTL Integration Customer Map")

	def test_rejects_missing(self):
		fake_db = SimpleNamespace(exists=lambda *args, **kwargs: False)
		with (
			patch(
				"ltl_quote.freight.doctype.ltl_integration_settings.ltl_integration_settings.frappe.db",
				fake_db,
			),
			self.assertRaises(frappe.ValidationError),
		):
			_validate_target_doctype("Not A DocType")


if __name__ == "__main__":
	unittest.main()
