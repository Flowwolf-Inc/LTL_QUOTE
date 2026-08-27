app_name = "ltl_quote"
app_title = "LTL Quote"
app_publisher = "LTL Quote"
app_description = "Digital freight network and intelligent LTL rating engine"
app_email = "admin@ltlquote.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "ltl_quote",
# 		"logo": "/assets/ltl_quote/logo.png",
# 		"title": "LTL Quote",
# 		"route": "/ltl_quote",
# 		"has_permission": "ltl_quote.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/ltl_quote/css/ltl_quote.css"
# app_include_js = "/assets/ltl_quote/js/ltl_quote.js"

# include js, css files in header of web template
# web_include_css = "/assets/ltl_quote/css/ltl_quote.css"
# web_include_js = "/assets/ltl_quote/js/ltl_quote.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "ltl_quote/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {
	"LTL Quote Request": "freight/doctype/ltl_quote_request/ltl_quote_request.js",
	"LTL Shipment": "freight/doctype/ltl_shipment/ltl_shipment.js",
}
doctype_list_js = {
	"Dayton Packaging Type": "freight/doctype/dayton_packaging_type/dayton_packaging_type_list.js",
	"Dayton Shipping Class": "freight/doctype/dayton_shipping_class/dayton_shipping_class_list.js",
	"Dayton Accessorial": "freight/doctype/dayton_accessorial/dayton_accessorial_list.js",
	"Dayton Response Accessorial": "freight/doctype/dayton_response_accessorial/dayton_response_accessorial_list.js",
	"Dayton State Province": "freight/doctype/dayton_state_province/dayton_state_province_list.js",
	"Dayton LTL Accessorial": "freight/doctype/dayton_ltl_accessorial/dayton_ltl_accessorial_list.js",
	"Dayton Service Center": "freight/doctype/dayton_service_center/dayton_service_center_list.js",
}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "ltl_quote/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "ltl_quote.utils.jinja_methods",
# 	"filters": "ltl_quote.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "ltl_quote.install.before_install"
after_install = "ltl_quote.install.after_install"
after_migrate = "ltl_quote.install.after_migrate"

# Uninstallation
# ------------

# before_uninstall = "ltl_quote.uninstall.before_uninstall"
# after_uninstall = "ltl_quote.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "ltl_quote.utils.before_app_install"
# after_app_install = "ltl_quote.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "ltl_quote.utils.before_app_uninstall"
# after_app_uninstall = "ltl_quote.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "ltl_quote.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "ltl_quote.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

scheduler_events = {
	"hourly": [
		# Generic tasks ignore Dayton shipments to prevent double-polling API consumption.
		"ltl_quote.tasks.refresh_active_shipment_tracking",
		# Targeted Dayton tracking extraction (kept off dayton.py to avoid import cycles).
		"ltl_quote.tasks.sync_all_active_shipments",
	],
}

# Testing
# -------

# before_tests = "ltl_quote.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "ltl_quote.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "ltl_quote.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "ltl_quote.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["ltl_quote.utils.before_request"]
# after_request = ["ltl_quote.utils.after_request"]

# Job Events
# ----------
# before_job = ["ltl_quote.utils.before_job"]
# after_job = ["ltl_quote.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"ltl_quote.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

