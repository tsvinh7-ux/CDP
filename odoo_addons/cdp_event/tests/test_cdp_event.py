from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestEventTouchpoints(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("cdp.enabled", "1")
        now = fields.Datetime.now()
        cls.event = cls.env["event.event"].create({"name": "Webinar Implant", "date_begin": now + timedelta(days=3),
                                                   "date_end": now + timedelta(days=3, hours=2)})
        cls.partner = cls.env["res.partner"].create({"name": "BS. Hòa", "mobile": "0903111222"})

    def events(self, reg, event_type):
        return self.env["cdp.outbox"].search([("event_type", "=", event_type), ("res_model", "=", "event.registration"),
                                              ("res_id", "=", reg.id)])

    def test_register_attend_once(self):
        reg = self.env["event.registration"].create({"event_id": self.event.id, "partner_id": self.partner.id})
        self.assertEqual(len(self.events(reg, "event.registered")), 1)
        props = self.events(reg, "event.registered").payload["properties"]
        self.assertEqual(props["event_name"], "Webinar Implant")
        reg.action_set_done()
        self.assertEqual(len(self.events(reg, "event.attended")), 1)
        # Deterministic ids: replaying the same transition never duplicates in Core.
        self.assertEqual(self.events(reg, "event.attended").event_id,
                         self.env["cdp.outbox"]._history_event_id(reg, "event.attended"))

    def test_guest_registration_without_partner(self):
        reg = self.env["event.registration"].create({"event_id": self.event.id, "name": "Khách",
                                                     "email": "guest@clinic.vn"})
        reg.action_cancel()
        ev = self.events(reg, "event.cancelled")
        if reg.partner_id:  # Odoo may link an existing partner by e-mail
            self.assertTrue(ev)
        else:
            self.assertEqual(ev.payload["subject"]["identifiers"]["email"], "guest@clinic.vn")
