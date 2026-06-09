# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

from frappe import _


def get_data():
	return [
		{
			"module_name": "Freight",
			"color": "blue",
			"icon": "octicon octicon-package",
			"type": "module",
			"label": _("LTL Quote"),
		}
	]
