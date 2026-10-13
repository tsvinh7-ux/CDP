from odoo import api, models

# Provider codes of Bizapp's VNPay/SePay modules. Until they are known, the
# provider name is used ("VNPay ...", "SePay ...").
KNOWN_PROVIDERS = ("vnpay", "sepay")


class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

    def _cdp_provider_key(self):
        self.ensure_one()
        if self.provider_code in KNOWN_PROVIDERS:
            return self.provider_code
        name = (self.provider_id.name or "").lower()
        return next((k for k in KNOWN_PROVIDERS if k in name), self.provider_code)

    def _cdp_emit_for_orders(self, event_type):
        outbox = self.env["cdp.outbox"]
        for tx in self:
            for order in tx.sale_order_ids:
                props = order._cdp_order_props()
                props.update(provider=tx._cdp_provider_key(), transaction=tx.reference,
                             amount=tx.amount if len(tx.sale_order_ids) == 1 else order.amount_total)
                outbox._emit(event_type, order.partner_id, order, props)

    @api.model_create_multi
    def create(self, vals_list):
        txs = super().create(vals_list)
        # The customer picked a payment method (VNPay redirect, SePay QR
        # transfer): the order now waits for money.
        txs._cdp_emit_for_orders("order.placed")
        return txs

    def _set_done(self, state_message=None, extra_allowed_states=()):
        before = {tx.id: tx.state for tx in self}
        res = super()._set_done(state_message=state_message, extra_allowed_states=extra_allowed_states)
        self.filtered(lambda tx: tx.state == "done" and before.get(tx.id) != "done")._cdp_emit_for_orders("order.paid")
        return res
