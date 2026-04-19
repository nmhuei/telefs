# TeleFS 🚀

**Biến Telegram thành Remote Filesystem chuyên nghiệp (Remote Filesystem)**

TeleFS là một công cụ CLI mạnh mẽ, bảo mật và giàu tính năng, biến "Saved Messages" (Tin nhắn đã lưu) trên tài khoản Telegram của bạn thành một ổ đĩa ảo không giới hạn. Với TeleFS, bạn có thể quản lý dữ liệu giống như một hệ điều hành thực thụ ngay trên terminal.

---

## ✨ Tính năng nổi bật

- **📦 Quản lý tệp tin khổng lồ**: Tự động chia tệp thành các mảnh nhỏ (mặc định 100MB) để lưu trữ các tệp lớn vượt giới hạn 2GB của Telegram.
- **⚡️ Truyền tải hiệu năng cao**: Tăng tốc upload/download với cơ chế đa luồng (concurrency) và hỗ trợ tiếp tục (Resume) từ mảnh bị lỗi.
- **🛡 Bảo mật tuyệt đối (Zero-Knowledge)**: 
  - Toàn bộ dữ liệu được mã hóa **AES-256 GCM** trên máy khách trước khi gửi đi.
  - Hỗ trợ giải mã các tệp cũ (Legacy) mã hóa bằng Fernet.
  - Mỗi tệp có một khóa mã hóa ngẫu nhiên và `nonce` duy nhất.
  - Hỗ trợ lưu trữ khóa bảo mật qua **System Keyring** (macOS Keychain, Windows Credential Manager).
- **🔄 Khử trùng lặp (Deduplication)**: Tự động nhận diện nội dung tệp đã tồn tại để tránh upload lại, giúp tiết kiệm băng thông tối đa.
- **💾 Copy siêu tốc (Virtual Copy)**: Sao chép tệp/thư mục từ xa mà không cần tải lên lại (tận dụng lại các mảnh đã có trên Telegram).
- **🖥 Interactive Shell**: Chế độ shell tương tác với prompt động, tự động gợi ý đường dẫn (tab completion) và hiển thị bảng biểu `Rich` chuyên nghiệp.
- **🌳 Enhanced Tree**: Lệnh `tree` mạnh mẽ với các cờ `-a`, `-d`, `-s`, `-h` và icons sinh động.

---

## 📦 Cài đặt

Yêu cầu: **Python 3.8+** và **Node.js**.

```bash
npm install -g @nmhuei/telefs
```

---

## 🚀 Hướng dẫn sử dụng

### 1. Cấu hình ban đầu
Chạy lệnh `login` để thiết lập thông tin API của bạn (lấy tại [my.telegram.org](https://my.telegram.org)).

```bash
telefs login
```

### 2. Bảng lệnh tham chiếu (Command Reference)

| Lệnh | Chức năng | Ví dụ |
| :--- | :--- | :--- |
| `ls [-l] [-a]` | Liệt kê tệp tin (hỗ trợ nhiều đường dẫn) | `telefs ls /Photos /Docs -l` |
| `cd <path>` | Thay đổi thư mục làm việc (ghi nhớ vĩnh viễn) | `telefs cd /Documents` |
| `tree [path] [-a] [-d] [-s] [-h]` | Hiển thị cây thư mục nâng cao | `tree / -ash` |
| `upload <local> [remote]` | Tải tệp/thư mục từ máy lên Telegram | `telefs upload ./anh.jpg /Photos` |
| `download <remote> [local]` | Tải tệp từ Telegram về máy | `telefs download /file.zip ./` |
| `cat <path>` | Xem nội dung tệp văn bản trực tiếp | `telefs cat /notes.txt` |
| `cp <src...> <dst> [-r]` | Sao chép đa tệp/thư mục (Virtual Copy) | `telefs cp /D1 /D2 /Backup -r` |
| `mv <src...> <dst>` | Di chuyển hoặc đổi tên (hỗ trợ đa tệp) | `telefs mv /F1 /F2 /Target` |
| `rm <path...> [-r] [-f]` | Xóa đa tệp/thư mục vĩnh viễn | `telefs rm -r /Tmp /Logs -f` |
| `find [path] -name "pat"` | Tìm kiếm tệp theo mẫu (wildcard) | `telefs find / -name "*.jpg"` |
| `du [path]` | Tính toán dung lượng thư mục | `telefs du /Documents` |
| `info <path>` | Xem thông tin kỹ thuật chi tiết của tệp | `telefs info /movie.mkv` |
| `status` / `quota` | Kiểm tra trạng thái kết nối và thống kê | `telefs status` |
| `config [get/set]` | Quản lý cấu hình TeleFS | `telefs config list` |

---

## 🛠 Chế độ Shell tương tác

Chỉ cần gõ `telefs` để vào môi trường làm việc tập trung:

```text
telefs:/ > cd Work
telefs:/Work > ls -l
telefs:/Work > find . -name "*.pdf"
telefs:/Work > cat report.txt
telefs:/Work > exit
```

---

## 🔒 Kiến trúc bảo mật & Lưu trữ

- **Local Metadata**: Thông tin cấu trúc thư mục được lưu tại `~/.config/telefs/metadata.db` (SQLite).
- **Persistent CWD**: Thư mục hiện hành được TeleFS ghi nhớ giúp bạn tiếp tục công việc ngay lập tức.
- **Reference Counting**: Khi bạn xóa một tệp đã được sao chép nhiều lần (Virtual Copy), TeleFS đủ thông minh để giữ lại dữ liệu trên Telegram cho đến khi bản sao cuối cùng bị xóa.

---

## ⚖️ Giấy phép
MIT - Phát triển bởi **Antigravity**.
