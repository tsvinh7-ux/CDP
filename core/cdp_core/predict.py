"""Predictions: next touchpoints and products/services a customer will buy.

Two per-tenant models, retrained daily by the worker (or POST /v1/admin/models/train):

* next_touchpoint – 2nd-order Markov chain over touchpoint sequences with
  back-off to 1st order and the global distribution; median delay per transition.
  Quality: accuracy@3 on the last transition of held-out customers.

* product_propensity – logistic regression P(buy item within 30 days | features
  at T0), trained on point-in-time snapshots (features from events before
  T0 = now-30d, label = bought in (T0, now]) so nothing from the future leaks in.
  Quality: ROC AUC on held-out customers. With too little history it falls back
  to documented rule weights and says so (mode = "heuristic").
"""

import math
import random
import statistics
from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Event, MLModel, Profile, Tenant, utcnow
from .touchpoints import classify, describe
from .traits import aware, compute_person, recommend

HORIZON_DAYS = 30
FEATURES = ["cooc", "popularity", "views", "in_cart", "peer", "progression", "engagement", "active_7d", "orders"]
HEURISTIC_WEIGHTS = {"bias": -4.0, "cooc": 3.0, "popularity": 1.0, "views": 2.5, "in_cart": 3.5, "peer": 1.2,
                     "progression": 1.5, "engagement": 1.0, "active_7d": 0.8, "orders": 0.3}
REASONS = {
    "cooc": "người mua giống bạn thường mua tiếp", "popularity": "được nhiều người chọn",
    "views": "đã xem nhiều lần", "in_cart": "đang có trong giỏ hàng", "peer": "đồng nghiệp đã học/dùng",
    "progression": "bước tiếp theo trong lộ trình", "engagement": "đang tương tác tích cực",
    "active_7d": "hoạt động trong 7 ngày qua", "orders": "đã từng mua",
}


# --------------------------------------------------------------------------- shared helpers

def purchases(events: list) -> list[tuple]:
    """(time, item, amount) bought/acquired, first time per item."""
    out, seen = [], set()
    for ev in sorted(events, key=lambda e: aware(e.occurred_at)):
        p = (ev.payload or {}).get("properties") or {}
        at = aware(ev.occurred_at)
        rows = []
        if ev.event_type == "order.paid":
            items = p.get("items") or []
            for it in items:
                if it.get("name"):
                    rows.append((it["name"], it.get("price") or (p.get("amount") or 0) / max(1, len(items))))
        elif ev.event_type == "course.enrolled" and p.get("course_name"):
            rows.append((p["course_name"], None))
        elif ev.event_type == "treatment_plan.accepted":
            groups = p.get("service_groups") or []
            rows += [(g, (p.get("amount") or 0) / max(1, len(groups))) for g in groups]
        elif ev.event_type == "procedure.completed" and p.get("service_group"):
            rows.append((p["service_group"], p.get("amount")))
        for item, amount in rows:
            if item not in seen:
                seen.add(item)
                out.append((at, item, amount))
    return out


def _sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))


def _auc(scores: list[float], labels: list[int]) -> float | None:
    pos = sum(labels)
    neg = len(labels) - pos
    if not pos or not neg:
        return None
    ranked = sorted(zip(scores, labels), key=lambda x: x[0])
    rank_sum, i = 0.0, 0
    while i < len(ranked):  # average ranks for ties
        j = i
        while j + 1 < len(ranked) and ranked[j + 1][0] == ranked[i][0]:
            j += 1
        avg = (i + j) / 2 + 1
        rank_sum += sum(avg for k in range(i, j + 1) if ranked[k][1])
        i = j + 1
    return (rank_sum - pos * (pos + 1) / 2) / (pos * neg)


# --------------------------------------------------------------------------- next touchpoint (Markov)

def _markov_fit(seqs: list[tuple[list[str], list]]) -> dict:
    uni, bi, tri, delays = Counter(), defaultdict(Counter), defaultdict(Counter), defaultdict(list)
    for keys, times in seqs:
        for i in range(1, len(keys)):
            uni[keys[i]] += 1
            bi[keys[i - 1]][keys[i]] += 1
            if i >= 2:
                tri[f"{keys[i - 2]}|{keys[i - 1]}"][keys[i]] += 1
            delays[f"{keys[i - 1]}|{keys[i]}"].append(max(0.0, (times[i] - times[i - 1]).total_seconds() / 86400))
    all_delays = [d for v in delays.values() for d in v]
    return {
        "uni": dict(uni), "bi": {k: dict(v) for k, v in bi.items()}, "tri": {k: dict(v) for k, v in tri.items()},
        "delay": {k: round(statistics.median(v), 2) for k, v in delays.items()},
        "delay_all": round(statistics.median(all_delays), 2) if all_delays else 3.0,
    }


def next_touchpoints(model: dict, keys: list[str], k: int = 3) -> list[dict]:
    if not model or not keys:
        return []
    dists = []
    for weight, counts in ((0.6, model["tri"].get("|".join(keys[-2:])) if len(keys) >= 2 else None),
                           (0.3, model["bi"].get(keys[-1])), (0.1, model["uni"])):
        if counts and sum(counts.values()) >= (3 if weight == 0.6 else 1):
            total = sum(counts.values())
            dists.append((weight, {key: c / total for key, c in counts.items()}))
    if not dists:
        return []
    norm = sum(w for w, _d in dists)
    score: Counter = Counter()
    for w, dist in dists:
        for key, p in dist.items():
            score[key] += w / norm * p
    out = []
    for key, p in score.most_common(k):
        channel, stage, direction, label = describe(key)
        days = model["delay"].get(f"{keys[-1]}|{key}", model["delay_all"])
        out.append({"key": key, "label": label, "channel": channel, "stage": stage, "direction": direction,
                    "probability": round(p, 3), "expected_in_days": round(days, 1)})
    return out


# Generic page views are kept on the journey but not modelled: they mostly echo the
# specific event next to them (page + "Course Viewed") and would inflate accuracy.
NOT_MODELLED = {"web.page"}


def model_keys(touchpoints) -> tuple[list[str], list]:
    keys, times = [], []
    for tp in touchpoints:
        if tp.key in NOT_MODELLED:
            continue
        if not keys or keys[-1] != tp.key:
            keys.append(tp.key)
            times.append(tp.at)
    return keys, times


def _markov_accuracy(train: list, test: list) -> tuple[float | None, float | None, int]:
    """accuracy@3 of the model and of the naive "3 most common touchpoints" baseline."""
    model = _markov_fit(train)
    baseline = [k for k, _c in Counter(model["uni"]).most_common(3)]
    hits = base_hits = total = 0
    for keys, _times in test:
        if len(keys) < 3:
            continue
        predicted = [p["key"] for p in next_touchpoints(model, keys[:-1], 3)]
        hits += keys[-1] in predicted
        base_hits += keys[-1] in baseline
        total += 1
    if not total:
        return None, None, 0
    return round(hits / total, 3), round(base_hits / total, 3), total


# --------------------------------------------------------------------------- product propensity

def _cooc(owned_by_profile: list[set]) -> tuple[dict, dict]:
    """P(b | a) and popularity from sets of owned items."""
    count, pair = Counter(), Counter()
    for owned in owned_by_profile:
        for a in owned:
            count[a] += 1
            for b in owned:
                if a != b:
                    pair[(a, b)] += 1
    n = max(1, len(owned_by_profile))
    cond: dict = defaultdict(dict)
    for (a, b), c in pair.items():
        cond[a][b] = round(c / count[a], 4)
    return dict(cond), {i: round(c / n, 4) for i, c in count.items()}


def features(traits: dict, item: str, owned: set, ctx: dict, peers: set) -> list[float]:
    cooc = max((ctx["cooc"].get(a, {}).get(item, 0.0) for a in owned), default=0.0)
    course_id = ctx["course_ids"].get(item)
    views = (traits.get("course_views_14d") or {}).get(course_id, 0) if course_id else 0
    cart_items = {i.get("name") for i in ((traits.get("open_cart") or {}).get("items") or [])}
    nexts = {c.get("name") for c in traits.get("next_courses") or []}
    if traits.get("clinic_next_course"):
        nexts.add(traits["clinic_next_course"].get("name"))
    seen = traits.get("last_seen_at")
    active = 1.0 if seen and aware(ctx["now"]) - _dt(seen) <= timedelta(days=7) else 0.0
    return [
        cooc,
        ctx["popularity"].get(item, 0.0),
        min(1.0, math.log1p(views) / 2),
        1.0 if item in cart_items else 0.0,
        1.0 if item in peers else 0.0,
        1.0 if item in nexts else 0.0,
        (traits.get("engagement_score") or 0) / 100,
        active,
        min(1.0, math.log1p(traits.get("orders_count") or 0) / 2),
    ]


def _dt(value):
    from .traits import parse_dt
    return parse_dt(value)


def _fit_logistic(X: list[list[float]], y: list[int], l2: float = 1e-2, iters: int = 25) -> list[float]:
    """Ridge-regularised logistic regression by Newton/IRLS (bias first)."""
    d = len(X[0]) + 1
    w = [0.0] * d
    rows = [[1.0] + x for x in X]
    for _ in range(iters):
        grad = [0.0] * d
        hess = [[0.0] * d for _ in range(d)]
        for r, label in zip(rows, y):
            p = _sigmoid(sum(wi * ri for wi, ri in zip(w, r)))
            g, h = p - label, p * (1 - p)
            for i in range(d):
                grad[i] += g * r[i]
                ri_h = r[i] * h
                for j in range(i, d):
                    hess[i][j] += ri_h * r[j]
        for i in range(d):
            for j in range(i):
                hess[i][j] = hess[j][i]
            hess[i][i] += l2
            if i:
                grad[i] += l2 * w[i]
        step = _solve(hess, grad)
        w = [wi - si for wi, si in zip(w, step)]
        if max(abs(s) for s in step) < 1e-6:
            break
    return w


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        if abs(m[col][col]) < 1e-12:
            continue
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    return [m[i][n] / m[i][i] if abs(m[i][i]) > 1e-12 else 0.0 for i in range(n)]


def predict_products(model: dict, traits: dict, owned: set, peers: set, now, k: int = 3) -> tuple[list[dict], float]:
    if not model:
        return [], 0.0
    ctx = {"cooc": model["cooc"], "popularity": model["popularity"], "course_ids": model["course_ids"], "now": now}
    w = model["weights"]
    scored = []
    for item in model["items"]:
        if item in owned:
            continue
        x = features(traits, item, owned, ctx, peers)
        z = w[0] + sum(wi * xi for wi, xi in zip(w[1:], x))
        p = _sigmoid(z)
        contrib = sorted(((wi * xi, f) for wi, xi, f in zip(w[1:], x, FEATURES) if wi * xi > 0.05), reverse=True)
        price = model["prices"].get(item)
        scored.append({"item": item, "probability": round(p, 3),
                       "expected_revenue": round(p * price) if price else None, "price": price,
                       "reasons": [REASONS[f] for _c, f in contrib[:3]]})
    scored.sort(key=lambda s: -s["probability"])
    any_p = 1 - math.prod(1 - s["probability"] for s in scored) if scored else 0.0
    return scored[:k], round(min(0.99, any_p), 3)


# --------------------------------------------------------------------------- training

def _snapshot_inputs(session: Session, tenant: Tenant):
    profiles = session.scalars(select(Profile).where(Profile.tenant_id == tenant.id, Profile.kind == "person",
                                                     Profile.merged_into.is_(None))).all()
    by_profile = defaultdict(list)
    for ev in session.scalars(select(Event).where(Event.tenant_id == tenant.id, Event.profile_id.is_not(None),
                                                  Event.kind.notin_(["entity.upserted", "entity.deleted"]))):
        by_profile[ev.profile_id].append(ev)
    return [p for p in profiles if not (p.traits or {}).get("erased")], by_profile


def train(session: Session, tenant: Tenant, seed: int = 7) -> dict:
    from .pipeline import _catalog, _partner_fields

    now = utcnow()
    t0 = now - timedelta(days=HORIZON_DAYS)
    cfg = tenant.settings or {}
    profiles, by_profile = _snapshot_inputs(session, tenant)
    rng = random.Random(seed)
    test_ids = {p.id for p in profiles if rng.random() < 0.2}

    # ---- next touchpoint
    seqs = {}
    for p in profiles:
        tps = sorted((tp for tp in (classify(e) for e in by_profile.get(p.id, [])) if tp), key=lambda t: t.at)
        keys, times = model_keys(tps)
        if len(keys) >= 2:
            seqs[p.id] = (keys, times)
    accuracy, baseline_acc, evaluated = _markov_accuracy([s for pid, s in seqs.items() if pid not in test_ids],
                                                         [s for pid, s in seqs.items() if pid in test_ids])
    markov = _markov_fit(list(seqs.values()))
    _store(session, tenant.id, "next_touchpoint", markov, {
        "customers": len(seqs), "transitions": sum(len(k) - 1 for k, _t in seqs.values()),
        "accuracy_at_3": accuracy, "baseline_accuracy_at_3": baseline_acc, "test_customers": evaluated,
        "baseline_most_common": _baseline(markov)})

    # ---- product propensity
    catalog = _catalog(session, tenant.id)
    course_ids = {c["name"]: cid for cid, c in catalog.items() if c.get("name")}
    all_buys = {p.id: purchases(by_profile.get(p.id, [])) for p in profiles}
    prices = defaultdict(list)
    for buys in all_buys.values():
        for _at, item, amount in buys:
            if amount:
                prices[item].append(float(amount))
    items = sorted({i for buys in all_buys.values() for _a, i, _m in buys} | set(course_ids))
    owned_t0 = {pid: {i for at, i, _a in buys if at < t0} for pid, buys in all_buys.items()}
    cooc_t0, pop_t0 = _cooc([s for s in owned_t0.values() if s])
    account_items_t0 = defaultdict(set)
    for p in profiles:
        if p.account_partner_id:
            account_items_t0[p.account_partner_id] |= owned_t0.get(p.id, set())
    ctx = {"cooc": cooc_t0, "popularity": pop_t0, "course_ids": course_ids, "now": t0}
    X, y, groups = [], [], []
    for p in profiles:
        before = [e for e in by_profile.get(p.id, []) if aware(e.occurred_at) < t0]
        if not before:
            continue
        traits = compute_person(_partner_fields(session, tenant.id, p.partner_id), before, t0, cfg)
        if catalog:
            traits.update(recommend(traits, catalog, cfg))
        owned = owned_t0[p.id]
        bought_after = {i for at, i, _a in all_buys[p.id] if at >= t0}
        peers = account_items_t0.get(p.account_partner_id, set()) - owned
        for item in items:
            if item in owned:
                continue
            X.append(features(traits, item, owned, ctx, peers))
            y.append(1 if item in bought_after else 0)
            groups.append(p.id in test_ids)
    positives = sum(y)
    metrics = {"rows": len(X), "positives": positives, "horizon_days": HORIZON_DAYS, "items": len(items)}
    if positives >= 15 and len(y) - positives >= 50:
        Xtr = [x for x, g in zip(X, groups) if not g]
        ytr = [v for v, g in zip(y, groups) if not g]
        Xte = [x for x, g in zip(X, groups) if g]
        yte = [v for v, g in zip(y, groups) if g]
        w_eval = _fit_logistic(Xtr, ytr) if sum(ytr) and len(ytr) - sum(ytr) else None
        if w_eval:
            scores = [_sigmoid(w_eval[0] + sum(a * b for a, b in zip(w_eval[1:], x))) for x in Xte]
            metrics["auc"] = round(_auc(scores, yte) or 0, 3) if yte else None
            metrics["test_rows"] = len(yte)
            metrics["test_positives"] = sum(yte)
            # Naive baseline: rank items by popularity only.
            metrics["baseline_auc_popularity"] = round(_auc([x[1] for x in Xte], yte) or 0, 3) if yte else None
        weights = _fit_logistic(X, y)
        metrics["mode"] = "logistic_regression"
    else:
        weights = [HEURISTIC_WEIGHTS["bias"]] + [HEURISTIC_WEIGHTS[f] for f in FEATURES]
        metrics["mode"] = "heuristic"
        metrics["note"] = "chưa đủ lịch sử mua (cần ≥ 15 lượt mua trong 30 ngày gần nhất) – dùng trọng số quy tắc"
    metrics["weights"] = dict(zip(["bias"] + FEATURES, [round(v, 3) for v in weights]))
    # Serving statistics use all history.
    cooc_all, pop_all = _cooc([{i for _a, i, _m in b} for b in all_buys.values() if b])
    _store(session, tenant.id, "product_propensity", {
        "weights": weights, "features": FEATURES, "items": items, "cooc": cooc_all, "popularity": pop_all,
        "course_ids": course_ids, "prices": {i: round(statistics.median(v)) for i, v in prices.items()},
    }, metrics)
    session.info.pop("cdp_models", None)
    session.flush()
    return {"next_touchpoint": session.get(MLModel, (tenant.id, "next_touchpoint")).metrics, "product_propensity": metrics}


def _baseline(markov: dict) -> list[str]:
    return [k for k, _c in Counter(markov["uni"]).most_common(3)]


def _store(session: Session, tenant_id: str, kind: str, payload: dict, metrics: dict):
    row = session.get(MLModel, (tenant_id, kind)) or MLModel(tenant_id=tenant_id, kind=kind, version=0)
    row.payload, row.metrics, row.trained_at = payload, metrics, utcnow()
    row.version = (row.version or 0) + 1
    session.add(row)


def load(session: Session, tenant_id: str) -> dict:
    cache = session.info.setdefault("cdp_models", {})
    if tenant_id not in cache:
        rows = session.scalars(select(MLModel).where(MLModel.tenant_id == tenant_id)).all()
        cache[tenant_id] = {r.kind: r.payload for r in rows}
    return cache[tenant_id]


def predictions_for(session: Session, tenant: Tenant, traits: dict, events: list, now) -> dict:
    """Prediction traits for one profile (empty when no model was trained yet)."""
    models = load(session, tenant.id)
    out = {}
    if models.get("next_touchpoint"):
        tps = sorted((tp for tp in (classify(e) for e in events) if tp), key=lambda t: t.at)
        out["next_touchpoints"] = next_touchpoints(models["next_touchpoint"], model_keys(tps)[0])
    if models.get("product_propensity"):
        owned = {i for _a, i, _m in purchases(events)}
        peers = {c.get("name") for c in (traits.get("clinic") or {}).get("staff_courses") or []} - owned
        products, any_p = predict_products(models["product_propensity"], traits, owned, peers, now)
        out["predicted_products"] = products
        out["purchase_probability_30d"] = any_p
    return out
