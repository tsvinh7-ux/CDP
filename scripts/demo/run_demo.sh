#!/usr/bin/env bash
# End-to-end demo: Odoo 17 (DMAG eLearning) ⇄ CDP Core ⇄ (MOCK) Facebook/Zalo.
#
# Prerequisites (see docs/huong-dan-chay-demo.md):
#   - PostgreSQL with roles odoo/odoo (superuser) and cdp/cdp, database cdp_core owned by cdp
#   - Odoo 17 source in $ODOO_SRC with a virtualenv in $ODOO_PY
#   - CDP Core virtualenv in $CORE_PY (pip install -e "core[dev]" playwright)
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
RUNTIME="${RUNTIME:-/home/user/runtime}"
ODOO_SRC="${ODOO_SRC:-$RUNTIME/odoo17}"
ODOO_PY="${ODOO_PY:-$RUNTIME/odoo-venv/bin/python}"
CORE_PY="${CORE_PY:-$RUNTIME/core-venv/bin/python}"
ODOO_CONF="${ODOO_CONF:-$RUNTIME/odoo.conf}"
LOGS="$RUNTIME/logs"; mkdir -p "$LOGS"
DEMO="$REPO/scripts/demo"

export CDP_DATABASE_URL="${CDP_DATABASE_URL:-postgresql+psycopg://cdp:cdp@localhost:5432/cdp_core}"
export CDP_ADMIN_TOKEN="${CDP_ADMIN_TOKEN:-demo-admin}"
export CDP_CORE_URL="${CDP_CORE_URL:-http://localhost:8000}"
export CDP_SECRET="${CDP_SECRET:-demo-secret}"

odoo_shell() { DEMO_STEP="$1" "$ODOO_PY" "$ODOO_SRC/odoo-bin" shell -c "$ODOO_CONF" -d dmag --no-http \
                 < "$DEMO/odoo_steps.py" 2>>"$LOGS/odoo-shell.log" | grep -v '^>>>' || true; }
run_jobs() { curl -s -X POST "$CDP_CORE_URL/v1/admin/run-jobs?tenant=dmag&token=$CDP_ADMIN_TOKEN" \
               | "$CORE_PY" -c "import json,sys; r=json.load(sys.stdin)['dmag']; print('   → Odoo:', {k: r[k] for k in ('segments','profiles','triggers')}, '| kênh:', r.get('destinations'))"; }
wait_http() { until curl -s -o /dev/null "$1"; do sleep 1; done; }

echo "== 1. CDP Core + MOCK Facebook/Zalo (API thật không truy cập được từ máy demo)"
(cd "$REPO/core" && "$CORE_PY" -m cdp_core.cli create-tenant --id dmag --name "DMAG Academy" \
   --secret "$CDP_SECRET" --write-key wk_dmag --odoo-url http://localhost:8069 \
   --settings '{"abandon_minutes": 1, "transfer_minutes": 1}')
(cd "$REPO/core" && "$CORE_PY" -m uvicorn cdp_core.app:app --port 8000 > "$LOGS/core.log" 2>&1 &)
("$CORE_PY" "$DEMO/mock_providers.py" > "$LOGS/mock.log" 2>&1 &)
wait_http "$CDP_CORE_URL/health"; wait_http http://localhost:9000/log
"$CORE_PY" "$DEMO/core_steps.py" destinations

echo "== 2. Odoo: cài database dmag + các module CDP (vài phút)"
"$ODOO_PY" "$ODOO_SRC/odoo-bin" -c "$ODOO_CONF" -d dmag \
  -i cdp_base,cdp_website,cdp_education,cdp_marketing_bridge,cdp_dental,cdp_crm,cdp_event,payment_demo --stop-after-init \
  > "$LOGS/dmag-init.log" 2>&1

echo "== 3. Dữ liệu mẫu: khóa học, 2 phòng khám, nhân sự đã học/mua (VNPay, SePay), consent"
odoo_shell seed
"$ODOO_PY" "$ODOO_SRC/odoo-bin" -c "$ODOO_CONF" -d dmag > "$LOGS/odoo.log" 2>&1 &
wait_http http://localhost:8069/web/login

echo "== 4. Odoo outbox → CDP Core → hồ sơ/phân khúc → đẩy về Odoo + kênh kích hoạt"
odoo_shell send
run_jobs
echo "== 5. Odoo: quy tắc khi khách vào phân khúc (việc cho sales, danh sách email)"
odoo_shell rules

echo "== 6. Website (Playwright): ẩn danh từ Facebook → đăng nhập → thêm giỏ; BS. Hòa đặt đơn SePay"
"$CORE_PY" "$DEMO/browser.py" journey
odoo_shell sepay_order
odoo_shell send
run_jobs

echo "== 7. Zalo OA webhook: BS. Minh quan tâm OA và chia sẻ số điện thoại"
"$CORE_PY" "$DEMO/core_steps.py" zalo

echo "== 8. Chờ quá ngưỡng 1 phút: 'bỏ giỏ hàng' và 'chờ chuyển khoản'"
sleep 70
odoo_shell send
run_jobs

echo "== 9. Thêm điểm chạm: CRM (lead, cuộc gọi), sự kiện (webinar), email marketing (gửi/mở/click)"
odoo_shell touchpoints
odoo_shell send
curl -s -X POST "$CDP_CORE_URL/v1/admin/models/train?tenant=dmag&token=$CDP_ADMIN_TOKEN" > /dev/null
run_jobs

echo "== 10. Tenant thứ hai: phòng khám dùng Dental ERP"
"$CORE_PY" "$DEMO/core_steps.py" dental

echo "== 11. Tenant MÔ PHỎNG cho dashboard & mô hình dự đoán (${SIM_PEOPLE:-2000} người, 120 ngày – dữ liệu giả)"
(cd "$REPO/core" && "$CORE_PY" -m cdp_core.simulate --people "${SIM_PEOPLE:-2000}" \
   | "$CORE_PY" -c "import ast,sys; r=ast.literal_eval(sys.stdin.read()); m=r['models']; print('   →', r['people'], 'người,', r['events'], 'sự kiện | độ chính xác top-3 điểm chạm', m['next_touchpoint'].get('accuracy_at_3'), '(baseline', m['next_touchpoint'].get('baseline_accuracy_at_3'), ') | AUC mua', m['product_propensity'].get('auc'), '(baseline', m['product_propensity'].get('baseline_auc_popularity'), ')')")

echo "== 12. Kết quả"
odoo_shell report
echo "-- Đã gửi tới (MOCK) Facebook/Zalo:"
"$CORE_PY" "$DEMO/core_steps.py" summary
"$CORE_PY" "$DEMO/browser.py" screens
echo "Odoo:        http://localhost:8069  (admin/admin; portal: minh@anphuc.vn/demo1234)"
echo "CDP console: $CDP_CORE_URL/console?token=$CDP_ADMIN_TOKEN&tenant=dmag"
echo "Dashboard mô phỏng: $CDP_CORE_URL/console?token=$CDP_ADMIN_TOKEN&tenant=dmag_sim"
