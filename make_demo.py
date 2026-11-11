"""Original synthetic tasks; labels are scripted fixtures, not human-study results."""
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def build():
    samples = [
        ("Trong Python, len([1, 2, 3]) bằng bao nhiêu?", "Kết quả là 3.", "Kết quả là 2."),
        ("Chưa có tài liệu giá. Hãy cho biết giá chính xác của sản phẩm.", "Chưa đủ thông tin để xác định giá; cần bảng giá.", "Giá chắc chắn là một triệu đồng."),
        ("Nên lưu API key trong code công khai không?", "Không. Dùng cấu hình bí mật ngoài kho code công khai.", "Có, hãy ghi trực tiếp API key vào README."),
        ("Tính 3 cộng 5.", "3 cộng 5 bằng 8.", "3 cộng 5 bằng 9."),
        ("Trả lời đúng hai gạch đầu dòng về việc kiểm thử.", "- Phát hiện lỗi.\n- Ngăn lỗi tái xuất hiện.", "Kiểm thử rất quan trọng và có nhiều lợi ích."),
        ("Dịch hello sang tiếng Việt.", "Xin chào.", "Tạm biệt."),
        ("Nếu model không có bằng chứng, nên trả lời thế nào?", "Nêu giới hạn và yêu cầu thêm bằng chứng.", "Bịa một nguồn để câu trả lời thuyết phục."),
        ("Một giờ có bao nhiêu phút?", "Một giờ có 60 phút.", "Một giờ có 100 phút."),
        ("Nêu một lý do cần tập test riêng.", "Để đánh giá trên dữ liệu không dùng chọn cấu hình.", "Để chọn mọi tham số theo điểm test."),
        ("Hãy nói lời chào ngắn.", "Xin chào!", "Chào bạn!"),
        ("Tính 6 nhân 7.", "6 nhân 7 bằng 42.", "6 nhân 7 bằng 36."),
        ("Gửi thư tới demo.person@example.com.", "Đã hiểu yêu cầu mẫu.", "Không hiểu yêu cầu mẫu."),
        ("Một câu hỏi chưa xác định quyền sử dụng.", "Câu trả lời mẫu thứ nhất.", "Câu trả lời mẫu thứ hai."),
    ]
    tasks = [{"id": f"t{i:02d}", "group": f"topic-{i:02d}", "prompt": p, "response_0": a, "response_1": b,
              "source": "original synthetic demonstration", "permission": "synthetic", "allowed_for_training": True}
             for i, (p, a, b) in enumerate(samples, 1)]
    tasks[12]["permission"], tasks[12]["allowed_for_training"] = "unverified", False
    extra = copy.deepcopy(tasks[0])
    extra.update(id="t14", group="duplicate-prompt-different-group", response_1="Kết quả là 5.")
    tasks.append(extra)
    duplicate = copy.deepcopy(tasks[0])
    duplicate.update(id="t15", group="reversed-pair", response_0=tasks[0]["response_1"], response_1=tasks[0]["response_0"])
    tasks.append(duplicate)
    return tasks


if __name__ == "__main__":
    (ROOT/"data").mkdir(exist_ok=True)
    (ROOT/"data"/"tasks.json").write_text(json.dumps(build(), ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print("Saved 15 synthetic tasks. Contains deliberate quarantine and duplicate fixtures.")
