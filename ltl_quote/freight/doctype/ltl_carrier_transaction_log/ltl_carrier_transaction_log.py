from frappe.model.document import Document
from frappe.utils import now_datetime


class LTLCarrierTransactionLog(Document):
	def before_insert(self):
		if not self.timestamp:
			self.timestamp = now_datetime()
