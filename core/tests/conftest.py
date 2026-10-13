import os
import uuid
from datetime import datetime, timezone

import pytest

from cdp_core import db
from cdp_core.jobs import create_tenant

SECRET = "test-secret"


@pytest.fixture()
def session(tmp_path):
    url = os.environ.get("CDP_TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'cdp.db'}"
    db.configure(url)
    from cdp_core.db import Base
    from cdp_core import models  # noqa: F401

    Base.metadata.drop_all(db.engine())
    Base.metadata.create_all(db.engine())
    with db.session_scope() as s:
        yield s


@pytest.fixture()
def tenant(session):
    t = create_tenant(session, "dmag", "DMAG", SECRET, "wk_test", None, {"abandon_minutes": 60})
    session.flush()
    return t


def msg(kind="event", event_type="course.enrolled", subject=None, obj=None, properties=None,
        occurred_at=None, seq=None):
    m = {
        "spec_version": "1.0",
        "event_id": str(uuid.uuid4()),
        "kind": kind,
        "event_type": event_type,
        "occurred_at": (occurred_at or datetime.now(timezone.utc)).isoformat(),
        "tenant_id": "dmag",
        "source": {"type": "odoo"},
        "properties": properties or {},
    }
    if seq is not None:
        m["seq"] = seq
    if subject:
        m["subject"] = subject
    if obj:
        m["object"] = obj
    return m


def partner_msg(pid, seq=1, **fields):
    return msg("entity.upserted", "entity.upserted", obj={"model": "res.partner", "id": pid},
               properties={"fields": fields}, seq=seq)
