// Copyright (c) 2026, LTL Quote and contributors
// For license information, please see license.txt

const SYNC_CONNECTORS = ["Dayton", "ArcBest API", "TForce", "SMC3"];

frappe.ui.form.on("LTL Carrier", {
	refresh(frm) {
		if (frm.is_new() || !SYNC_CONNECTORS.includes(frm.doc.connector_type)) {
			return;
		}

		frm.add_custom_button(__("Sync Accessorials from API"), () => sync_accessorials(frm));

		if (frm.doc.connector_type === "SMC3") {
			frm.add_custom_button(__("Sync Barcode Requirements"), () => sync_barcode_requirements(frm));
			frm.add_custom_button(__("Sync Dispatch Messages"), () => sync_dispatch_response_messages(frm));
		}
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

function sync_barcode_requirements(frm) {
	frm.call({
		method: "sync_barcode_requirements",
		doc: frm.doc,
		freeze: true,
		freeze_message: __("Fetching barcode requirements from SMC3..."),
		callback(r) {
			if (r.exc || !r.message) {
				return;
			}
			frm.refresh_field("smc3_network_carriers");
			frappe.show_alert(
				{
					message: r.message.message || __("Barcode requirements updated"),
					indicator: r.message.updated ? "green" : "blue",
				},
				7
			);
		},
	});
}

function sync_dispatch_response_messages(frm) {
	frm.call({
		method: "sync_dispatch_response_messages",
		doc: frm.doc,
		freeze: true,
		freeze_message: __("Fetching dispatch response messages from SMC3..."),
		callback(r) {
			if (r.exc || !r.message) {
				return;
			}
			frappe.show_alert(
				{
					message: r.message.message || __("Dispatch messages updated"),
					indicator: r.message.created || r.message.updated ? "green" : "blue",
				},
				7
			);
		},
	});
}
