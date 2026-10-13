from odoo import models


class SaleOrder(models.Model):
    _name = "sale.order"
    _inherit = ["sale.order", "cdp.sync.mixin"]

    def _cdp_order_props(self):
        self.ensure_one()
        lines = self.order_line.filtered(lambda l: not l.display_type)
        return {
            "order_id": self.id,
            "order_name": self.name,
            "amount": self.amount_total,
            "currency": self.currency_id.name,
            "website_id": self.website_id.id or None,
            "items": [
                {"product_id": l.product_id.id, "name": l.product_id.name, "qty": l.product_uom_qty,
                 "price": l.price_subtotal}
                for l in lines
            ],
            "utm_source": self.source_id.name or None,
            "utm_medium": self.medium_id.name or None,
            "utm_campaign": self.campaign_id.name or None,
        }

    def _cart_update(self, product_id, line_id=None, add_qty=0, set_qty=0, **kwargs):
        res = super()._cart_update(product_id, line_id=line_id, add_qty=add_qty, set_qty=set_qty, **kwargs)
        # Carts of anonymous visitors belong to the public partner and are
        # skipped by _emit; they become visible once the visitor logs in.
        self.env["cdp.outbox"]._emit("cart.updated", self.partner_id, self, self._cdp_order_props())
        return res

    def action_confirm(self):
        res = super().action_confirm()
        for order in self:
            if order.state == "sale":
                order.env["cdp.outbox"]._emit("order.confirmed", order.partner_id, order, order._cdp_order_props())
        return res

    def _action_cancel(self):
        res = super()._action_cancel()
        for order in self:
            order.env["cdp.outbox"]._emit("order.cancelled", order.partner_id, order, order._cdp_order_props())
        return res

    def _cdp_history_events(self):
        """Backfill: past paid orders (deterministic ids, historical dates)."""
        outbox = self.env["cdp.outbox"]
        for order in self.filtered(lambda o: o.state in ("sale", "done")):
            txs = order.transaction_ids.filtered(lambda t: t.state == "done")
            props = order._cdp_order_props()
            props["provider"] = txs[:1]._cdp_provider_key() if txs else "unknown"
            outbox._emit("order.paid", order.partner_id, order, props, occurred_at=order.date_order,
                         event_id=outbox._history_event_id(order, "order.paid"))
