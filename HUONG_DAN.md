# Dự án 19 — Human Feedback Data Pipeline

Mục tiêu: biến việc so sánh hai câu trả lời AI thành dữ liệu có thể truy nguồn,
kiểm tra chất lượng và xuất theo phiên bản. Không cần API key, GPU hoặc cài thư viện.
Code và dữ liệu mẫu đã được tạo sẵn. Dự án chưa huấn luyện DPO hay reward model.

## 1. Vào thư mục

```powershell
cd "C:\Users\LOQ\Documents\Codex\2026-11-06\cj\outputs\Human-Feedback-Data-Pipeline"
python -m venv .venv
```

Không cần Activate.ps1. Các lệnh sau dùng trực tiếp Python trong môi trường.

## 2. Kiểm tra code

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Kỳ vọng: `Ran 22 tests` và `OK`.

## 3. Tạo bộ demo

```powershell
.\.venv\Scripts\python.exe pipeline.py demo
```

Kỳ vọng trên DB mới:

```text
tasks: 15
ready_consensus: 10
disagreement: 1
tie: 1
pending: 1
quarantined: 2
```

- `ready_consensus`: hai lượt chấm cùng chọn một câu trả lời.
- `disagreement`: hai lượt chấm bất đồng, chưa được xuất.
- `tie`: hai câu được đánh giá ngang nhau, không xuất thành cặp nhị phân.
- `pending`: chưa có đủ hai lượt chấm dùng được.
- `quarantined`: có cờ cần cách ly, như thiếu khai báo quyền sử dụng hoặc giống email.

Ba tên `mai`, `nam`, `lead` là bí danh giả lập. Demo tự điền nhãn bằng kịch bản;
không phải kết quả của một nghiên cứu chấm độc lập bởi người thật.
DB lưu tại `private/feedback.db`. Chạy demo lần nữa không chấm đè lên dữ liệu có sẵn.

## 4. Xem và phân xử bất đồng

```powershell
.\.venv\Scripts\python.exe pipeline.py inspect-dispute --task t09 --adjudicator lead
```

Bạn sẽ thấy prompt, `response_0`, `response_1`, và hai lượt nhận xét.
Câu hỏi là lý do cần tập test riêng. Trong mẫu này:

- `response_0` nói đánh giá trên dữ liệu không dùng chọn cấu hình.
- `response_1` nói chọn mọi tham số theo điểm test.

Chọn câu 0 sau khi đọc nội dung:

```powershell
.\.venv\Scripts\python.exe pipeline.py adjudicate --task t09 --adjudicator lead --preference 0 --rationale "Tap test phai doc lap voi du lieu chon cau hinh."
```

Kỳ vọng: `adjudicated: t09`. Ở bước phân xử, `0/1` là tên câu canonical, không phải
A/B đã đảo cho người chấm. Không chạy phân xử lần hai: quyết định đã lưu là bất biến.
Nếu đã phân xử thành công và chạy lại bị báo trùng, tiếp tục bước 5.

## 5. Xem báo cáo chất lượng

```powershell
.\.venv\Scripts\python.exe pipeline.py quality
Start-Process reports/quality.html
```

Lúc này có thêm `ready_adjudicated: 1`; `disagreement` không còn xuất hiện trong
counts vì không còn task thuộc trạng thái đó. Các trạng thái khác giữ nguyên.

Reviewer agreement được tính trên nhãn ban đầu trước phân xử, sau khi quy đổi A/B
về câu canonical. Đây là thống kê mô tả, không chứng minh người chấm đúng.

```powershell
.\.venv\Scripts\python.exe pipeline.py audit
```

Kỳ vọng có số lượng events và `event_head_sha256`. Hash có thể khác giữa các lần
tạo DB vì thời gian thao tác khác nhau. Chuỗi hash giúp phát hiện thay đổi vô tình;
không chống được người có toàn quyền sửa cả DB và hash.

## 6. Xuất dataset phiên bản đầu

```powershell
.\.venv\Scripts\python.exe pipeline.py export --version v1
```

Kỳ vọng nếu chỉ thực hiện các bước trên:

```text
train: 7
dev: 1
test: 2
groups: 9
excluded: 5
```

Vì sao có 15 task nhưng chỉ xuất 10 cặp? Một task ngang điểm, một task chưa đủ
lượt chấm, hai task bị cách ly và một cặp trùng bị loại. Lý do nằm trong quality.json.
Các câu hỏi trùng nhau hoặc thuộc cùng group không bị chia sang các tập khác nhau.
Đây là bộ nhỏ để học quy trình, chưa đủ để kết luận chất lượng huấn luyện.

```powershell
Get-Content exports/v1/train.jsonl -Encoding utf8
Get-Content exports/v1/lineage.jsonl -Encoding utf8
Get-Content exports/v1/quality.json -Encoding utf8
```

Mỗi dòng train có ba trường:

- `prompt`: yêu cầu người dùng.
- `chosen`: câu được ưu tiên.
- `rejected`: câu ít được ưu tiên.

Đây là định dạng preference chuẩn có thể dùng làm đầu vào cho bước chuẩn bị DPO,
nhưng dự án này không tự tải hoặc huấn luyện model.

## 7. Kiểm tra bản xuất

```powershell
.\.venv\Scripts\python.exe pipeline.py verify --release exports/v1
$LASTEXITCODE
```

Kỳ vọng `decision: verified`, `pairs: 10`, `groups: 9` và mã thoát `0`.
Verify kiểm tra checksum, schema, dòng truy nguồn và việc chia tập; không khẳng định
nhãn đúng thực tế hoặc giấy phép dữ liệu đã được xác minh bên ngoài.

Không sửa file v1 để đổi kết quả. Muốn xuất lại, dùng phiên bản mới:

```powershell
.\.venv\Scripts\python.exe pipeline.py export --version v2
```

Chạy lại `--version v1` sẽ bị từ chối để tránh ghi đè bản cũ. Một release mới có thể
chia lại group khi dữ liệu tăng; cần cố định test registry riêng trước nghiên cứu thực.

## 8. Thực hành tự chấm một task

Đọc `RUBRIC.md` trước, rồi:

```powershell
.\.venv\Scripts\python.exe pipeline.py import --file data/manual_task.json
.\.venv\Scripts\python.exe pipeline.py label --task manual01 --reviewer mai
```

Terminal hiển thị câu hỏi và hai câu trả lời A/B. Với câu hỏi `len([])`, hãy chọn
câu nói kết quả bằng 0, bất kể nó đang nằm ở A hay B. Nhập chữ `A` hoặc `B`, nhấn
Enter; sau đó nhập nhận xét cụ thể tối thiểu 10 ký tự, nhấn Enter.

Thử giao diện người chấm thứ hai:

```powershell
.\.venv\Scripts\python.exe pipeline.py label --task manual01 --reviewer nam
```

Thứ tự A/B có thể khác. Đọc lại nội dung trước khi chọn. Tự thao tác cả hai bí danh
chỉ là luyện quy trình; khi thu dữ liệu thật cần người chấm độc lập và hệ thống xác thực.

```powershell
.\.venv\Scripts\python.exe pipeline.py quality
Start-Process reports/quality.html
```

Nếu đã thêm task này thì số task là 16, nên không yêu cầu báo cáo mới vẫn giống bộ
15 task ban đầu. Bản xuất v1 giữ nguyên. Xuất phiên bản mới nếu muốn chứa nhãn mới.

## 9. Đọc code

1. `make_demo.py`: dữ liệu task giả lập.
2. `store.py`: SQLite, assignment A/B, lưu nhãn, phân xử và audit events.
3. `release.py`: trạng thái chất lượng, agreement, nhóm chia tập và export/verify.
4. `pipeline.py`: các lệnh CLI và báo cáo HTML.
5. `tests/test_pipeline.py`: chấm trùng, cạnh tranh ghi, bất đồng, rò rỉ và checksum.

Không dùng dữ liệu nhiệm vụ nội bộ của Outlier hay khách hàng để nhập vào repo công khai.
Các thư mục private, reports, exports và DB đã được loại trừ khỏi Git.
