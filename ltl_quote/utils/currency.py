# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

import frappe

DEFAULT_QUOTE_CURRENCY = "INR"


def get_quote_currency() -> str:
	settings = frappe.get_single("LTL Platform Settings")
	return settings.get("quote_currency") or DEFAULT_QUOTE_CURRENCY
