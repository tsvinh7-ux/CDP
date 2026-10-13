# Data Contract v1: Odoo ↔ CDP Core

> Trạng thái: **v1, đã cài đặt trong `cdp_base`/CDP Core** (Bizapp review phần mapping và Dental ERP) · Ngày: 13/10/2026
> Phạm vi: định dạng và giao thức trao đổi dữ liệu giữa các module `cdp_*` trong Odoo 17 và CDP Core.
> Đi kèm: [`de-xuat-kien-truc-cdp.md`](de-xuat-kien-truc-cdp.md)

## 1. Nguyên tắc

1. **Odoo không bao giờ gọi đồng bộ (synchronous) sang Core trong transaction nghiệp vụ.** Mọi sự kiện đi qua bảng outbox rồi được gửi bởi job nền. Core có dừng thì Odoo vẫn chạy bình thường.
2. **At-least-once + idempotent.** Một sự kiện có thể được gửi nhiều lần; Core khử trùng theo `event_id`.
3. **Thứ tự theo thực thể.** Với cùng một bản ghi Odoo, Core áp dụng theo `seq` (id tăng dần của outbox); sự kiện cũ hơn đến sau thì bị bỏ qua đối với snapshot.
4. **Tối thiểu hóa dữ liệu.** Chỉ gửi field có trong mapping đã duyệt. Mặc định không gửi field văn bản tự do (ghi chú, mô tả), và tuyệt đối không gửi dữ liệu lâm sàng.
5. **Có version.** Mọi message có `spec_version`; thay đổi không tương thích ngược thì tăng major version, Core hỗ trợ song song ít nhất 2 major version.

---

## 2. Bảng outbox trong Odoo (`cdp.outbox`)

| Field | Kiểu | Ghi chú |
|------|-----|--------|
| `id` | integer | Dùng làm `seq` |
| `event_id` | char (UUIDv7) | Sinh khi ghi; khóa idempotency |
| `kind` | selection | `entity.upserted`, `entity.deleted`, `event` |
| `event_type` | char | Ví dụ `order.paid`, `course.completed` (mục 5) |
| `res_model`, `res_id` | char, integer | Bản ghi gốc |
| `payload` | json | Thân message đã dựng sẵn (mục 3) |
| `state` | selection | `pending` → `sent` / `failed` → `dead` |
| `attempts`, `next_attempt_at`, `last_error` | | Retry lũy thừa: 1, 2, 4, 8… phút, tối đa 6 giờ; quá 48 giờ chuyển `dead` và báo admin |
| `create_date` | datetime | |

- Ghi outbox **trong cùng transaction** với thao tác nghiệp vụ (override method hoặc `create`/`write`/`unlink` của model trong mapping).
- Nhiều lần `write` lên cùng bản ghi trong một transaction được **gộp thành một** `entity.upserted` (dùng `self.env.cr.precommit`).
- Bản ghi `sent` được dọn sau 14 ngày.

---

## 3. Envelope chung

```json
{
  "spec_version": "1.0",
  "event_id": "0192f3a4-7c1e-7b2a-9f00-3d5e8a1b2c4d",
  "seq": 1048576,
  "kind": "event",
  "event_type": "order.paid",
  "occurred_at": "2026-10-13T08:15:30.123Z",
  "tenant_id": "dmag",
  "source": {
    "type": "odoo",
    "instance": "dmag-prod",
    "database": "dmag",
    "odoo_version": "17.0",
    "module_version": "cdp_base 17.0.1.0.0"
  },
  "subject": {
    "model": "res.partner",
    "id": 123,
    "identifiers": {
      "phone": "+84901234567",
      "email": "bs.lan@example.com",
      "portal_user_id": 456
    }
  },
  "object": { "model": "sale.order", "id": 789 },
  "properties": { },
  "context": { "company_id": 1, "website_id": 1, "user_id": 2, "lang": "vi_VN" }
}
```

| Field | Bắt buộc | Mô tả |
|------|:-------:|------|
| `subject` | Có (trừ khi sự kiện không gắn với người) | Người mà sự kiện thuộc về. `identifiers` chỉ chứa giá trị đã chuẩn hóa (mục 6) |
| `object` | Không | Thực thể nghiệp vụ liên quan |
| `properties` | Có | Thuộc tính riêng theo `event_type` (mục 5) |
| Thời gian | | Luôn là UTC, ISO 8601 |
| Tiền | | Số nguyên VND trong `amount` và `currency: "VND"`. Nếu là ngoại tệ thì thêm `amount_company_currency` |

### 3.1. `entity.upserted`

Snapshot các field trong mapping của một bản ghi:

```json
{
  "kind": "entity.upserted",
  "event_type": "entity.upserted",
  "object": { "model": "res.partner", "id": 123 },
  "properties": {
    "write_date": "2026-10-13T08:15:29Z",
    "fields": {
      "name": "Nguyễn Thị Lan",
      "is_company": false,
      "parent_id": 98,
      "phone": "+84901234567",
      "email": "bs.lan@example.com",
      "city": "Hà Nội",
      "x_job_role": "dentist"
    }
  }
}
```

- Many2one gửi dạng id. Many2many/one2many chỉ gửi khi có trong mapping, dạng danh sách id.
- `entity.deleted` chỉ có `object` và có thể kèm `properties.merged_into` (khi gộp partner).

---

## 4. Giao thức truyền

### 4.1. Odoo → Core

```
POST https://ingest.<cdp-domain>/v1/batch
Content-Type: application/json
X-CDP-Tenant: dmag
X-CDP-Key-Id: key_2026_01
X-CDP-Timestamp: 1760343330
X-CDP-Signature: sha256=<hex(HMAC_SHA256(secret, timestamp + "." + body))>

{ "messages": [ <envelope>, ... ] }   // tối đa 500 message hoặc 1 MB
```

| Phản hồi | Ý nghĩa | Odoo xử lý |
|---------|--------|-----------|
| `200` + `{"accepted":[...],"duplicates":[...],"rejected":[{"event_id":..,"error":..}]}` | Đã nhận | `accepted`/`duplicates` → `sent`; `rejected` → `dead` (lỗi dữ liệu, không retry) |
| `401` / `403` | Sai chữ ký/khóa | Dừng gửi, báo admin |
| `413` | Lô quá lớn | Chia đôi lô, gửi lại |
| `429` / `5xx` / timeout | Tạm thời | Retry theo lịch |

- Chữ ký hết hạn sau 5 phút (chống replay).
- Khóa xoay vòng bằng `X-CDP-Key-Id`, trong thời gian chuyển đổi chấp nhận cả khóa cũ và mới.

### 4.2. Core → Odoo

Endpoint do `cdp_base` cung cấp, xác thực bằng HMAC như trên (đã cài trong MVP). Khi lên production, bổ sung **Odoo API key** của user kỹ thuật `cdp_bot`, chỉ có quyền trên model `cdp.*`.

| Endpoint | Mục đích |
|---------|---------|
| `POST /cdp/v1/profiles` | Upsert `cdp.profile` theo lô (chỉ hồ sơ thay đổi). Mỗi hồ sơ kèm **danh sách đầy đủ** `segments`: đây là nguồn chuẩn cho thành viên phân khúc, idempotent và tự sửa nếu lỡ mất một lần đẩy |
| `POST /cdp/v1/segments/<key>/members` | Thêm/bớt thành viên theo delta (dùng cho thao tác hàng loạt thủ công) |
| `PUT /cdp/v1/segments/<key>` | Tạo/cập nhật định nghĩa hiển thị của phân khúc (tên, mô tả, số lượng) |
| `POST /cdp/v1/triggers` | Trigger real-time cho Marketing Automation/CRM |
| `GET /cdp/v1/health` | Kiểm tra kết nối, version module, độ trễ outbox |

Ví dụ:

```json
// POST /cdp/v1/profiles
{ "profiles": [
  { "partner_id": 123, "cdp_id": "prf_01J9...", "kind": "person", "updated_at": "2026-10-13T02:00:00Z",
    "segments": ["clinic_owners", "owner_staff_basic_no_advanced"],
    "traits": { "rfm_segment": "champion", "total_spent": 18500000, "engagement_score": 72,
                "clinic": { "name": "Nha khoa An Phúc", "learners_count": 2, "staff_course_tags": ["Cơ bản"] },
                "timeline": [ { "at": "...", "label": "Xem khóa Implant Nâng cao" } ] } }
]}

// POST /cdp/v1/segments/checkout_abandoned_24h/members
{ "add": [123, 124], "remove": [99], "as_of": "2026-10-13T08:20:00Z" }

// POST /cdp/v1/triggers
{ "trigger_id": "trg_01J9...", "key": "checkout_abandoned", "partner_id": 123,
  "occurred_at": "2026-10-13T08:20:00Z",
  "data": { "order_id": 789, "amount": 4500000, "course_names": ["Implant cơ bản"] } }
```

- Odoo ghi theo lô, với context `tracking_disable=True, mail_notrack=True`, chỉ ghi vào model `cdp.*`, **không ghi vào `res.partner`**.
- Trigger idempotent theo `trigger_id`.
- `partner_id` không tồn tại (đã xóa/gộp) → trả về trong danh sách `unknown`. Core sẽ đánh dấu để đối soát.

### 4.3. Backfill & đối soát

- **Backfill**: lệnh `cdp_base` "Đồng bộ toàn bộ" sinh `entity.upserted` cho mọi bản ghi trong mapping, đi theo kênh outbox với độ ưu tiên thấp, 1.000 bản ghi/lô.
- **Đối soát hằng đêm** (tự động mỗi 24 giờ): Core gọi `GET /cdp/v1/reconcile?model=res.partner&after_id=<id>&limit=5000` (phân trang theo id) để nhận danh sách `(id, write_date)` rồi so với dữ liệu đã có. Bản ghi lệch được Core yêu cầu gửi lại bằng `POST /cdp/v1/resync` với danh sách id.

---

## 5. Danh mục sự kiện v1

### 5.1. Chung (`cdp_base`)

| `event_type` | Nguồn Odoo | `properties` chính |
|-------------|-----------|-------------------|
| `entity.upserted` / `entity.deleted` | Model trong mapping | Mục 3.1 |
| `partner.merged` | Gộp partner (wizard merge) | `merged_ids`, `into_id` |
| `account.signed_up` | Tạo `res.users` portal | `signup_method`, `utm_*` |
| `consent.updated` | Form consent / thay đổi opt-out | `purpose` (`care`, `marketing`, `ads`, `analytics`), `granted`, `channel`, `evidence_ref` |
| `order.created` | `sale.order` tạo | `order_id`, `amount`, `currency`, `lines[]` (`product_id`, `qty`, `price`), `website_id`, `utm_*` |
| `cart.updated` | `sale.order._cart_update` (website) | `order_id`, `order_name`, `amount`, `items[]` |
| `order.placed` | `payment.transaction` được tạo (khách chọn VNPay hoặc SePay, tiền chưa về) | `order_id`, `amount`, `provider` (`vnpay`, `sepay`), `transaction` |
| `order.confirmed` | `sale.order.action_confirm` | `order_id`, `amount` (không ghi đè trạng thái đã thanh toán) |
| `order.paid` | `payment.transaction` → `done` (VNPay IPN / webhook SePay) | `order_id`, `amount`, `provider` (`vnpay`, `sepay`), `transaction` |
| `order.cancelled` / `refund.issued` | | `order_id`, `amount`, `reason_code` |
| `lead.created` / `lead.stage_changed` | `crm.lead` | `lead_id`, `stage`, `source`, `medium`, `campaign`, `user_id` |
| `marketing.message_sent` / `opened` / `clicked` / `bounced` / `unsubscribed` | Marketing Automation / mailing | `campaign_id`, `activity_id`, `channel` |
| `marketing.participant_completed` | Marketing Automation | `campaign_id` |
| `privacy.erasure_requested` | Nút "Yêu cầu xóa dữ liệu" (`cdp_base`) | `requested_by`. Core xóa dữ liệu, giữ tombstone, gỡ khỏi audience quảng cáo; Odoo đặt `cdp_excluded` |

### 5.2. Education (`cdp_education`)

| `event_type` | Nguồn Odoo | `properties` chính |
|-------------|-----------|-------------------|
| `course.enrolled` | `slide.channel.partner` tạo (bỏ qua giảng viên phụ trách) | `course_id`, `course_name`, `course_tags` (ví dụ `Cơ bản`, `Nâng cao`), `enroll_type` (`paid`, `free`, `invited`) |
| `lesson.completed` | `slide.slide.partner.completed` = True | `course_id`, `lesson_id`, `lesson_type` |
| `course.progressed` | `slide.channel.partner.completion` đổi qua mốc 25/50/75% | `course_id`, `completion` |
| `course.completed` | `completion` = 100 | `course_id`, `duration_days` |
| `quiz.submitted` | Quiz của slide / `survey.user_input` | `course_id`, `score`, `passed` |
| `certificate.issued` | `survey.user_input` chứng chỉ đạt | `course_id`, `certification_id` |
| `webinar.registered` / `webinar.attended` | Theo module sự kiện Bizapp dùng (`event.registration`?) | `event_id` |

### 5.3. Dental (`cdp_dental`): khung, chốt khi có thiết kế Dental ERP

| `event_type` | `properties` chính (không chứa dữ liệu lâm sàng) |
|-------------|-----------------------------------------------|
| `appointment.booked` / `rescheduled` / `cancelled` / `no_show` / `checked_in` | `appointment_id`, `branch_id`, `scheduled_at`, `service_group`, `channel` |
| `treatment_plan.proposed` / `accepted` / `declined` | `plan_id`, `service_groups[]`, `amount` |
| `procedure.completed` | `service_group` (vd. `scaling`, `filling`, `implant`, `ortho`, `whitening`), `branch_id`, `amount` |
| `installment.due` / `paid` / `overdue` | `plan_id`, `amount`, `due_date` |
| `recall.due` | `service_group`, `due_date` (tính bởi Core hoặc ERP, chốt sau) |

Trong `cdp_dental`, các sự kiện này được khai báo bằng **quy tắc cấu hình** (model, trường trạng thái, giá trị, đường dẫn tới khách hàng, thuộc tính), không cần sửa code Dental ERP. Thuộc tính lâm sàng (`diagnosis`, `note`…) bị chặn.

### 5.3b. Zalo OA (webhook vào Core: `POST /v1/webhooks/zalo/<tenant>`)

| `event_type` | Nguồn | Ghi chú |
|-------------|------|--------|
| `zalo.follow` / `zalo.unfollow` | Webhook OA | Định danh `zalo = <oa_id>:<user_id>` |
| `zalo.user_submit_info` | Khách chia sẻ thông tin với OA | SĐT trong `info.phone` dùng để ghép với partner |
| `zalo.user_send_text`… | Tin nhắn | Chỉ ghi nhận có tương tác, **không lưu nội dung** |

Chữ ký: `X-ZEvent-Signature: mac=sha256(app_id + body + timestamp + oa_secret_key)`.

### 5.4. Web SDK (gửi thẳng tới Core, không qua Odoo)

Theo cấu trúc Segment spec: `page`, `track`, `identify`.

| Sự kiện | `properties` |
|--------|-------------|
| `page` | `url`, `path`, `title`, `referrer`, `utm_*`, `fbclid`, `zclid` |
| `Course Viewed` | `course_id`, `course_name`, `price` |
| `Lesson Viewed` | `course_id`, `lesson_id` |
| `Video Opened` | `lesson_id`, `provider` (`youtube`, `html5`, `gdrive`) |
| `Video Progress` | `lesson_id`, `milestone` (25/50/75/100), `provider`. Chỉ có với YouTube và video HTML5 (NAS link trực tiếp); Google Drive không có API player |
| `Consent Updated` | `purposes` (ví dụ `{"analytics": true}`): khi khách chấp nhận cookie tùy chọn |
| `Lesson Time Spent` | `lesson_id`, `seconds` (gửi khi rời trang; dùng thay tiến độ cho video Google Drive) |
| `Products Searched` | `query` |
| `Product Added` | `product_id`, `course_id`, `price` |
| `Checkout Started` | `order_id`, `amount` |
| `Form Submitted` | `form_id` (không gửi nội dung form) |

`identify` chỉ được gọi với token ký bởi Odoo:

```
cdp_uid = base64url(partner_id "." exp) "." HMAC_SHA256(tenant_secret, partner_id "." exp)
```

Core từ chối `userId` không có chữ ký hợp lệ. Nhờ vậy không ai giả mạo được danh tính người khác từ trình duyệt.

---

## 6. Chuẩn hóa định danh (phía Odoo trước khi gửi)

| Định danh | Quy tắc |
|----------|--------|
| `phone` | E.164 `+84…`; chuyển đầu số di động 11 số cũ sang 10 số; bỏ ký tự không phải số; không hợp lệ thì gửi trong `phone_raw` và không đưa vào `identifiers` |
| `email` | Trim, lowercase; không hợp lệ thì bỏ |
| `portal_user_id` | `res.users.id` của tài khoản portal |
| `zalo_uid` | Theo OA (`zalo_oa_id:user_id`), nếu Odoo có lưu |
| `license_no` | Số chứng chỉ hành nghề, **chỉ khi có consent**, gửi dạng băm SHA-256 + salt theo tenant |

- Core mới là nơi quyết định ghép danh tính. Odoo không tự gộp partner dựa trên dữ liệu từ Core.
- **SĐT dùng chung** (tổng đài phòng khám) được Core đánh dấu. Odoo có thể gửi gợi ý qua `identifiers.phone_type`: `personal`/`work`/`unknown`.

---

## 7. Mapping mặc định (cấu hình được trong `cdp_base`)

| Model | Field gửi | Ghi chú |
|------|----------|--------|
| `res.partner` | `name`, `is_company`, `parent_id`, `type`, `phone`, `mobile`, `email`, `street`, `city`, `state_id`, `country_id`, `category_id`, `user_id`, `create_date` + field nghề nghiệp/phòng khám do Bizapp thêm | Không gửi `comment` |
| `sale.order` | `partner_id`, `state`, `amount_total`, `date_order`, `website_id`, `campaign_id`, `source_id`, `medium_id`, `order_line` (`product_id`, `product_uom_qty`, `price_subtotal`) | |
| `product.product` / `slide.channel` | `name`, `list_price`, `categ_id`, `channel_ids`/`product_id` | Dữ liệu danh mục cho gợi ý |
| `slide.channel.partner` | `channel_id`, `partner_id`, `completion`, `completed` | |
| `crm.lead` | `partner_id`, `stage_id`, `user_id`, `team_id`, `source_id`, `medium_id`, `campaign_id`, `expected_revenue`, `probability` | |

Field custom: admin thêm vào mapping qua giao diện. Thay đổi mapping được ghi log và cần quyền "CDP Manager".

---

## 8. Việc Bizapp cần xác nhận

1. Tên model/field thực tế của Marketing Automation (campaign, activity, participant, trace), cách chọn đối tượng, và có hook để thêm loại trigger mới không.
2. Các field custom trên `res.partner`, `sale.order` cần đưa vào mapping (nghề nghiệp, phòng khám, nguồn khách…).
3. Có dùng `queue_job` không? Phiên bản Odoo 17 chính xác và danh sách module đang cài.
4. Luồng thanh toán (provider nào, khi nào `payment.transaction` sang `done`).
5. Có sửa trực tiếp mã lõi Odoo không. Nếu có, danh sách file.
6. Webinar/lớp offline đang quản lý bằng module nào.
