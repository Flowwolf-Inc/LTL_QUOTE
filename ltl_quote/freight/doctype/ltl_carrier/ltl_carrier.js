// Copyright (c) 2026, LTL Quote and contributors
// For license information, please see license.txt

const SYNC_CONNECTORS = ["Dayton", "ArcBest API"];

frappe.ui.form.on("LTL Carrier", {
	refresh(frm) {
		if (frm.is_new() || !SYNC_CONNECTORS.includes(frm.doc.connector_type)) {
			return;
		}

		frm.add_custom_button(__("Sync Accessorials from API"), () => sync_accessorials(frm));
	},
});

function sync_accessorials(frm) {
	frm.call({
		method: "sync_accessorials",
		doc: frm.doc,
		freeze: true,
		freeze_message: __("Fetching accessorials from carrier..."),
		callback(r) {
			if (r.exc || !r.message) {
				return;
			}

			frm.refresh_field("accessorial_mappings");
			frappe.show_alert(
				{
					message: r.message.message || __("Accessorial mappings updated"),
					indicator: r.message.added || r.message.updated ? "green" : "blue",
				},
				7
			);
		},
	});
}
