frappe.listview_settings["Dayton Accessorial"] = {
	refresh(listview) {
		listview.page.add_inner_button(__("Sync from Dayton"), () => {
			frappe.show_alert({ message: __("Connecting to carrier..."), indicator: "blue" }, 3);
			frappe.call({
				method: "ltl_quote.api.shipping.sync_dayton_accessorials",
				freeze: true,
				freeze_message: __("Syncing accessorials from Dayton..."),
				callback(r) {
					const res = r.message || {};
					if (res.status === "success") {
						frappe.msgprint({
							title: __("Sync Complete"),
							indicator: "green",
							message: res.message || __("Accessorials synced."),
						});
						listview.refresh();
					}
				},
			});
		});
	},
};
