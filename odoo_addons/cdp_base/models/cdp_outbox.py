import json
import logging
import time
import uuid
from datetime import date, datetime, timedelta

import requests

from odoo import api, fields, models, release

from .tools import normalize_email, normalize_phone, sign

_logger = logging.getLogger(__name__)

SPEC_VERSION = "1.0"
BATCH_SIZE = 500
MAX_AGE_HOURS = 48


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


class CdpOutbox(models.Model):
    """Transactional outbox (docs/data-contract-v1.md §2).

    Rows are written in the same transaction as the business change and
    sent by a cron, so Odoo never waits on CDP Core and never loses or
    invents an event.
    """

    _name = "cdp.outbox"
    _description = "CDP Outbox"
    _order = "id"

    event_id = fields.Char(required=True, index=True, readonly=True)
    kind = fields.Selection(
        [("event", "Event"), ("entity.upserted", "Entity upserted"), ("entity.deleted", "Entity deleted")],
        required=True, readonly=True,
    )
    event_type = fields.Char(required=True, index=True, readonly=True)
    res_model = fields.Char(readonly=True)
    res_id = fields.Integer(readonly=True)
    payload = fields.Json(readonly=True)
    state = fields.Selection(
        [("pending", "Pending"), ("sent", "Sent"), ("failed", "Retrying"), ("dead", "Dead")],
        default="pending", required=True, index=True,
    )
    attempts = fields.Integer(default=0)
    next_attempt_at = fields.Datetime(default=fields.Datetime.now, index=True)
    sent_at = fields.Datetime()
    last_error = fields.Text()

    # ------------------------------------------------------------------ config

    @api.model
    def _cdp_config(self):
        icp = self.env["ir.config_parameter"].sudo()
        return {
            "enabled": icp.get_param("cdp.enabled") in ("1", "True", "true"),
            "core_url": (icp.get_param("cdp.core_url") or "").rstrip("/"),
            "tenant_id": icp.get_param("cdp.tenant_id") or "",
            "secret": icp.get_param("cdp.secret") or "",
            "instance": icp.get_param("cdp.instance") or self.env.cr.dbname,
        }

    @api.model
    def _cdp_enabled(self):
        return self._cdp_config()["enabled"]

    # ------------------------------------------------------------------ enqueue

    @api.model
    def _is_public_partner(self, partner):
        users = partner.sudo().with_context(active_test=False).user_ids
        return bool(users) and all(u._is_public() for u in users)

    @api.model
    def _subject(self, partner):
        partner = partner.sudo()
        users = partner.user_ids.filtered(lambda u: u.share) if "user_ids" in partner._fields else []
        identifiers = {
            "phone": normalize_phone(partner.mobile or partner.phone),
            "email": normalize_email(partner.email),
            "portal_user_id": users[:1].id if users else None,
        }
        return {
            "model": "res.partner",
            "id": partner.id,
            "identifiers": {k: v for k, v in identifiers.items() if v},
        }

    @api.model
    def _envelope(self, kind, event_type, record=None, partner=None, properties=None):
        cfg = self._cdp_config()
        msg = {
            "spec_version": SPEC_VERSION,
            "event_id": str(uuid.uuid4()),
            "kind": kind,
            "event_type": event_type,
            "occurred_at": fields.Datetime.now().isoformat() + "Z",
            "tenant_id": cfg["tenant_id"],
            "source": {
                "type": "odoo",
                "instance": cfg["instance"],
                "database": self.env.cr.dbname,
                "odoo_version": release.major_version,
                "module_version": "cdp_base " + (self.env.ref("base.module_cdp_base").sudo().latest_version or ""),
            },
            "properties": properties or {},
            "context": {"company_id": self.env.company.id, "user_id": self.env.uid, "lang": self.env.lang},
        }
        if partner:
            msg["subject"] = self._subject(partner)
        if record:
            msg["object"] = {"model": record._name, "id": record.id}
        return msg

    @api.model
    def _emit(self, event_type, partner=None, record=None, properties=None):
        """Queue a business event, e.g. ``_emit('order.paid', order.partner_id, order, {...})``."""
        if not self._cdp_enabled():
            return self.browse()
        if partner is not None and partner and self._is_public_partner(partner):
            return self.browse()  # anonymous website visitor: the web SDK covers them
        msg = self._envelope("event", event_type, record, partner, properties)
        return self.sudo().create({
            "event_id": msg["event_id"], "kind": "event", "event_type": event_type,
            "res_model": record._name if record else None, "res_id": record.id if record else None,
            "payload": json.loads(json.dumps(msg, default=_json_default)),
        })

    @api.model
    def _enqueue_entities(self, model_name, ids, deleted=False, merged_into=None):
        """Called at pre-commit with the ids touched in the transaction."""
        mapping = self.env["cdp.mapping"].sudo()._get_fields(model_name)
        vals = []
        records = self.env[model_name].sudo().browse(ids)
        if deleted:
            for rid in ids:
                msg = self._envelope("entity.deleted", "entity.deleted")
                msg["object"] = {"model": model_name, "id": rid}
                if merged_into and merged_into.get(rid):
                    msg["properties"] = {"merged_into": merged_into[rid]}
                vals.append(msg)
        else:
            for rec in records.exists():
                msg = self._envelope("entity.upserted", "entity.upserted", rec)
                msg["properties"] = {
                    "write_date": rec.write_date.isoformat() + "Z" if rec.write_date else None,
                    "fields": self.env["cdp.mapping"]._snapshot(rec, mapping),
                }
                vals.append(msg)
        if vals:
            self.sudo().create([{
                "event_id": m["event_id"], "kind": m["kind"], "event_type": m["event_type"],
                "res_model": model_name, "res_id": m["object"]["id"],
                "payload": json.loads(json.dumps(m, default=_json_default)),
            } for m in vals])

    # ------------------------------------------------------------------ send

    @api.model
    def _cron_send(self, limit=10):
        """Send up to `limit` batches; called every minute by ir.cron."""
        cfg = self._cdp_config()
        if not (cfg["enabled"] and cfg["core_url"] and cfg["secret"]):
            return
        for _i in range(limit):
            if not self._send_batch(cfg):
                break

    @api.model
    def _send_batch(self, cfg=None):
        cfg = cfg or self._cdp_config()
        self.env.cr.execute(
            """SELECT id FROM cdp_outbox
                WHERE state IN ('pending', 'failed') AND next_attempt_at <= (now() at time zone 'UTC')
                ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED""",
            [BATCH_SIZE],
        )
        rows = self.browse([r[0] for r in self.env.cr.fetchall()])
        if not rows:
            return False
        messages = [dict(r.payload, seq=r.id) for r in rows]
        body = json.dumps({"messages": messages}, ensure_ascii=False).encode()
        ts = int(time.time())
        headers = {
            "Content-Type": "application/json",
            "X-CDP-Tenant": cfg["tenant_id"],
            "X-CDP-Timestamp": str(ts),
            "X-CDP-Signature": sign(cfg["secret"], ts, body),
        }
        try:
            resp = requests.post(cfg["core_url"] + "/v1/batch", data=body, headers=headers, timeout=30)
        except requests.RequestException as exc:
            rows._mark_retry(str(exc))
            return False
        if resp.status_code == 413 and len(rows) > 1:
            # Should not happen with BATCH_SIZE, but never get stuck on it.
            rows[len(rows) // 2:]._mark_retry("batch too large, split", delay=False)
            return True
        if resp.status_code in (401, 403):
            rows._mark_retry(f"auth error {resp.status_code}: check CDP settings")
            _logger.error("CDP Core rejected credentials (%s)", resp.status_code)
            return False
        if resp.status_code != 200:
            rows._mark_retry(f"HTTP {resp.status_code}: {resp.text[:500]}")
            return False
        result = resp.json()
        ok = set(result.get("accepted", [])) | set(result.get("duplicates", []))
        rejected = {r.get("event_id"): r.get("error") for r in result.get("rejected", [])}
        now = fields.Datetime.now()
        rows.filtered(lambda r: r.event_id in ok).write({"state": "sent", "sent_at": now, "last_error": False})
        for row in rows.filtered(lambda r: r.event_id in rejected):
            row.write({"state": "dead", "last_error": rejected[row.event_id]})
        return True

    def _mark_retry(self, error, delay=True):
        now = fields.Datetime.now()
        for row in self:
            attempts = row.attempts + 1
            dead = (now - row.create_date) > timedelta(hours=MAX_AGE_HOURS)
            backoff = min(2 ** (attempts - 1), 360) if delay else 0
            row.write({
                "attempts": attempts,
                "state": "dead" if dead else "failed",
                "next_attempt_at": now + timedelta(minutes=backoff),
                "last_error": error,
            })

    def action_retry(self):
        self.write({"state": "pending", "next_attempt_at": fields.Datetime.now()})

    @api.model
    def action_send_now(self):
        self._cron_send()
        return True

    @api.autovacuum
    def _gc_sent(self):
        self.search([("state", "=", "sent"), ("sent_at", "<", fields.Datetime.now() - timedelta(days=14))]).unlink()
