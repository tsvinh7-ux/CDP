"""Endpoints called by CDP Core (data contract §4.2). All requests are HMAC-signed."""

import json
import logging
from datetime import datetime

from odoo import http
from odoo.http import request

from ..models.tools import verify

_logger = logging.getLogger(__name__)


def _json(data, status=200):
    return request.make_response(
        json.dumps(data, default=str), headers=[("Content-Type", "application/json")], status=status
    )


class CdpController(http.Controller):

    def _authenticate(self):
        """Return the parsed JSON body, or an error response."""
        cfg = request.env["cdp.outbox"].sudo()._cdp_config()
        body = request.httprequest.get_data()
        headers = request.httprequest.headers
        if headers.get("X-CDP-Tenant") != cfg["tenant_id"] or not verify(
            cfg["secret"], headers.get("X-CDP-Timestamp"), headers.get("X-CDP-Signature"), body
        ):
            return None, _json({"error": "invalid signature"}, 401)
        try:
            return (json.loads(body) if body else {}), None
        except ValueError:
            return None, _json({"error": "invalid JSON"}, 400)

    @http.route("/cdp/v1/health", type="http", auth="public", methods=["GET"], csrf=False, save_session=False)
    def health(self):
        _data, error = self._authenticate()
        if error:
            return error
        Outbox = request.env["cdp.outbox"].sudo()
        counts = {s: Outbox.search_count([("state", "=", s)]) for s in ("pending", "failed", "dead")}
        oldest = Outbox.search([("state", "in", ("pending", "failed"))], order="id", limit=1)
        lag = (datetime.now() - oldest.create_date).total_seconds() if oldest else 0
        version = request.env.ref("base.module_cdp_base").sudo().latest_version
        return _json({"status": "ok", "outbox": counts, "lag_seconds": lag, "module_version": version})

    @http.route("/cdp/v1/profiles", type="http", auth="public", methods=["POST"], csrf=False, save_session=False)
    def profiles(self):
        data, error = self._authenticate()
        if error:
            return error
        docs = data.get("profiles") or []
        unknown = request.env["cdp.profile"].sudo().with_context(tracking_disable=True)._cdp_upsert(docs)
        return _json({"updated": len(docs) - len(unknown), "unknown": unknown})

    @http.route("/cdp/v1/segments/<string:key>", type="http", auth="public", methods=["PUT"], csrf=False,
                save_session=False)
    def segment(self, key):
        data, error = self._authenticate()
        if error:
            return error
        Segment = request.env["cdp.segment"].sudo().with_context(active_test=False)
        seg = Segment._get_or_create([key])
        seg.write({
            "name": data.get("name") or key,
            "description": data.get("description"),
            "trigger_on_enter": bool(data.get("trigger_on_enter")),
            "active": data.get("active", True),
            "member_count": data.get("member_count") or 0,
        })
        return _json({"key": key, "id": seg.id})

    @http.route("/cdp/v1/segments/<string:key>/members", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def segment_members(self, key):
        data, error = self._authenticate()
        if error:
            return error
        env = request.env(su=True)
        seg = env["cdp.segment"]._get_or_create([key])
        Profile = env["cdp.profile"]
        add = env["res.partner"].browse(data.get("add") or []).exists()
        existing = Profile.search([("partner_id", "in", add.ids)])
        missing = add - existing.partner_id
        if missing:
            existing |= Profile.create([{"partner_id": p.id} for p in missing])
        existing.write({"segment_ids": [(4, seg.id)]})
        removed = Profile.search([("partner_id", "in", data.get("remove") or [])])
        removed.write({"segment_ids": [(3, seg.id)]})
        return _json({"added": len(existing), "removed": len(removed)})

    @http.route("/cdp/v1/triggers", type="http", auth="public", methods=["POST"], csrf=False, save_session=False)
    def trigger(self):
        data, error = self._authenticate()
        if error:
            return error
        env = request.env(su=True)
        Log = env["cdp.trigger.log"]
        if Log.search_count([("trigger_id", "=", data.get("trigger_id"))]):
            return _json({"status": "duplicate"})
        partner = env["res.partner"].browse(int(data.get("partner_id") or 0)).exists()
        seg = env["cdp.segment"].search([("key", "=", data.get("key"))], limit=1)
        log = Log.create({
            "trigger_id": data.get("trigger_id"), "key": data.get("key"), "segment_id": seg.id,
            "partner_id": partner.id, "data": data.get("data") or {},
        })
        log._dispatch()
        return _json({"status": log.state, "result": log.result})

    @http.route("/cdp/v1/reconcile", type="http", auth="public", methods=["GET"], csrf=False, save_session=False)
    def reconcile(self, model=None, since=None, limit=5000):
        """(id, write_date) of mapped records, for nightly reconciliation (§4.3)."""
        _data, error = self._authenticate()
        if error:
            return error
        env = request.env(su=True)
        if not model or not env["cdp.mapping"]._get_fields(model):
            return _json({"error": "model not mapped"}, 400)
        domain = [("write_date", ">=", since)] if since else []
        rows = env[model].with_context(active_test=False).search_read(
            domain, ["write_date"], order="id", limit=min(int(limit), 50000))
        return _json({"model": model, "records": [[r["id"], r["write_date"]] for r in rows]})

    @http.route("/cdp/v1/resync", type="http", auth="public", methods=["POST"], csrf=False, save_session=False)
    def resync(self):
        data, error = self._authenticate()
        if error:
            return error
        env = request.env(su=True)
        model = data.get("model")
        if not model or not env["cdp.mapping"]._get_fields(model):
            return _json({"error": "model not mapped"}, 400)
        ids = env[model].with_context(active_test=False).browse(data.get("ids") or []).exists().ids
        env["cdp.outbox"]._enqueue_entities(model, ids)
        return _json({"queued": len(ids)})
