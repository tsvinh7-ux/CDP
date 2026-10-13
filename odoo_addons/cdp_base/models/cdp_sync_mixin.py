from odoo import api, models

PENDING_KEY = "cdp.pending"


class CdpSyncMixin(models.AbstractModel):
    """Replicate mapped fields of a model to CDP Core.

    Changes are collected during the transaction and turned into one
    ``entity.upserted`` per record at pre-commit, so ten writes on the same
    partner produce a single message, and a rolled-back transaction produces
    none.
    """

    _name = "cdp.sync.mixin"
    _description = "CDP Sync Mixin"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._cdp_schedule("upsert")
        return records

    def write(self, vals):
        res = super().write(vals)
        tracked = self.env["cdp.mapping"]._get_fields(self._name)
        if tracked and set(vals) & set(tracked):
            self._cdp_schedule("upsert")
        return res

    def unlink(self):
        self._cdp_schedule("delete")
        return super().unlink()

    def _cdp_schedule(self, op, merged_into=None):
        if not self or self.env.context.get("cdp_no_sync"):
            return
        outbox = self.env["cdp.outbox"]
        if not outbox._cdp_enabled() or not self.env["cdp.mapping"]._get_fields(self._name):
            return
        precommit = self.env.cr.precommit
        if PENDING_KEY not in precommit.data:
            precommit.data[PENDING_KEY] = {}
            precommit.add(self._cdp_flush_pending)
        bucket = precommit.data[PENDING_KEY].setdefault(self._name, {"upsert": set(), "delete": set(), "merged": {}})
        bucket[op].update(self.ids)
        if op == "delete":
            bucket["upsert"].difference_update(self.ids)
            bucket["merged"].update(merged_into or {})

    def _cdp_flush_pending(self):
        # Pop first: anything scheduled while we run registers a new callback.
        pending = self.env.cr.precommit.data.pop(PENDING_KEY, {})
        outbox = self.env["cdp.outbox"].sudo()
        for model_name, bucket in pending.items():
            if bucket["upsert"]:
                outbox._enqueue_entities(model_name, sorted(bucket["upsert"]))
            if bucket["delete"]:
                outbox._enqueue_entities(model_name, sorted(bucket["delete"]), deleted=True,
                                         merged_into=bucket["merged"])
        self.env.flush_all()
