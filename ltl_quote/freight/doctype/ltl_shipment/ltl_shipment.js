// Copyright (c) 2026, LTL Quote and contributors
// For license information, please see license.txt

frappe.ui.form.on("LTL Shipment", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(__("Refresh Tracking"), () => {
				frappe.call({
					method: "refresh_tracking",
					doc: frm.doc,
					freeze: true,
					callback() {
						frm.reload_doc();
					},
				});
			}, __("Visibility"));

			if (frm.doc.status === "Booked" && frm.doc.dispatch_status !== "Acknowledged") {
				frm.add_custom_button(__("Dispatch to Carrier"), () => {
					frappe.call({
						method: "dispatch_to_carrier",
						doc: frm.doc,
						freeze: true,
						callback() {
							frm.reload_doc();
						},
					});
				}, __("Actions"));
			}
		}
	},
});
