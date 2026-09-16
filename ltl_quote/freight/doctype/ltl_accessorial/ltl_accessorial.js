// Copyright (c) 2026, LTL Quote and contributors
// For license information, please see license.txt

frappe.ui.form.on("LTL Accessorial", {
	refresh(frm) {
		if (frm.is_new() || !frm.doc.name) return;
		const name = frm.doc.name;
		window.__ltl_open_accessorial = name;
		frappe.route_options = {
			ltl_view: "accessorial",
			ltl_name: name,
		};
		frappe.set_route("ltl-quote");
		setTimeout(() => {
			const page = frappe.pages && frappe.pages["ltl-quote"];
			const dash = page && (page.ltl_dashboard || (page.wrapper && page.wrapper.ltl_dashboard));
			if (dash && typeof dash.open_accessorial_detail === "function") {
				window.__ltl_open_accessorial = null;
				dash.open_accessorial_detail(name);
			}
		}, 150);
	},
});
