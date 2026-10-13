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
    def _is_internal_partner(self, partner):
        """Employees are not customers: excluded unless cdp.include_internal_users is set."""
        if self.env["ir.config_parameter"].sudo().get_param("cdp.include_internal_users") in ("1", "True", "true"):
            return False
        return any(not u.share for u in partner.sudo().with_context(active_test=False).user_ids)

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
    def _envelope(self, kind, event_type, record=None, partner=None, properties=None, occurred_at=None,
                  event_id=None):
        cfg = self._cdp_config()
        msg = {
            "spec_version": SPEC_VERSION,
            "event_id": event_id or str(uuid.uuid4()),
            "kind": kind,
            "event_type": event_type,
            "occurred_at": (occurred_at or fields.Datetime.now()).isoformat() + "Z",
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
    def _emit(self, event_type, partner=None, record=None, properties=None, occurred_at=None, event_id=None):
        """Queue a business event, e.g. ``_emit('order.paid', order.partner_id, order, {...})``.

        Backfills pass the historical `occurred_at` and a deterministic `event_id`
        (see `_history_event_id`) so a re-run never duplicates events in CDP.
        """
        if not self._cdp_enabled():
            return self.browse()
        if partner is not None and partner and self._is_public_partner(partner):
            return self.browse()  # anonymous website visitor: the web SDK covers them
        if partner and partner.sudo().cdp_excluded and event_type != "privacy.erasure_requested":
            return self.browse()  # erasure requested: nothing about this person leaves Odoo
        if partner and self._is_internal_partner(partner):
            return self.browse()
        msg = self._envelope("event", event_type, record, partner, properties, occurred_at, event_id)
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
            if model_name == "res.partner":
                records = records.filtered(lambda p: not p.cdp_excluded and not self._is_internal_partner(p))
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
                WHERE state IN ('pending', 'failed') AND next_attempt_at <= (clock_timestamp() at time zone 'UTC')
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

    # ------------------------------------------------------------------ backfill

    @api.model
    def _history_event_id(self, record, event_type):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"odoo://{self.env.cr.dbname}/{record._name}/{record.id}/{event_type}"))

    @api.model
    def _cdp_backfill_steps(self):
        """Ordered (kind, model) steps; vertical add-ons append their history steps."""
        mappings = self.env["cdp.mapping"].sudo().search([("active", "=", True)])
        # Partners first so that history events find their profile.
        models = sorted(mappings.mapped("model_name"), key=lambda m: m != "res.partner")
        return [("entities", m) for m in models if m in self.env]

    @api.model
    def action_backfill(self):
        """Start a full (re)synchronisation, processed in chunks by the backfill cron."""
        state = {"steps": [[kind, model, 0] for kind, model in self._cdp_backfill_steps()]}
        self.env["ir.config_parameter"].sudo().set_param("cdp.backfill_state", json.dumps(state))
        cron = self.env.ref("cdp_base.ir_cron_cdp_backfill", raise_if_not_found=False)
        if cron:
            cron.sudo().write({"active": True})
            cron.sudo()._trigger()
        return True

    @api.model
    def _cron_backfill(self, chunk=1000, max_chunks=20):
        icp = self.env["ir.config_parameter"].sudo()
        state = json.loads(icp.get_param("cdp.backfill_state") or "{}")
        steps = state.get("steps") or []
        done_chunks = 0
        while steps and done_chunks < max_chunks:
            kind, model, last_id = steps[0]
            Model = self.env[model].sudo().with_context(active_test=False)
            domain = [("id", ">", last_id)]
            if model == "res.partner":
                domain.append(("cdp_excluded", "=", False))
            records = Model.search(domain, order="id", limit=chunk)
            if not records:
                steps.pop(0)
                continue
            if kind == "entities":
                self._enqueue_entities(model, records.ids)
            else:
                records._cdp_history_events()
            steps[0][2] = records[-1].id
            done_chunks += 1
            icp.set_param("cdp.backfill_state", json.dumps({"steps": steps}))
            if not self.env.context.get("cdp_no_commit"):
                self.env.cr.commit()  # long backfills progress chunk by chunk
        icp.set_param("cdp.backfill_state", json.dumps({"steps": steps}))
        if not steps:
            cron = self.env.ref("cdp_base.ir_cron_cdp_backfill", raise_if_not_found=False)
            if cron:
                cron.sudo().write({"active": False})
        elif not self.env.context.get("cdp_no_retrigger"):
            self.env.ref("cdp_base.ir_cron_cdp_backfill")._trigger()
        return steps

    @api.autovacuum
    def _gc_sent(self):
        self.search([("state", "=", "sent"), ("sent_at", "<", fields.Datetime.now() - timedelta(days=14))]).unlink()
