# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""Schema for Revenova sync identity fields on Carrier Quote.

These fields also live on the Carrier Quote DocType JSON. The dicts below are
the Custom Field equivalent if they ever need to be added to another DocType
via fixtures (`hooks.py` → `fixtures = ["Custom Field"]`).
"""

from __future__ import annotations

CARRIER_QUOTE_DOCTYPE = "Carrier Quote"

# Custom Field fixtures — equivalent of the native DocType fields.
CARRIER_QUOTE_CUSTOM_FIELDS = [
	{
		"doctype": "Custom Field",
		"dt": CARRIER_QUOTE_DOCTYPE,
		"fieldname": "revenova_id",
		"label": "Revenova Id",
		"fieldtype": "Data",
		"unique": 1,
		"length": 18,
		"in_list_view": 1,
		"in_standard_filter": 1,
		"insert_after": "sync_status",
		"description": "Salesforce/Revenova record Id (15 or 18 character).",
	},
	{
		"doctype": "Custom Field",
		"dt": CARRIER_QUOTE_DOCTYPE,
		"fieldname": "synced_from_revenova",
		"label": "Synced From Revenova",
		"fieldtype": "Check",
		"default": "0",
		"hidden": 1,
		"insert_after": "revenova_id",
		"description": "Set for the duration of an inbound Revenova save to block outbound echo.",
	},
]
