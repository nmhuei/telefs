# TeleFS 🚀

**Sử dụng Telegram làm Hệ thống tệp từ xa (Remote Filesystem)**

TeleFS là một công cụ CLI mạnh mẽ, bảo mật và đáng tin cậy, biến "Saved Messages" (Tin nhắn đã lưu) trên Telegram của bạn thành một ổ đĩa ảo. Bạn có thể lưu trữ, quản lý và tải tệp trực tiếp từ tài khoản Telegram với các tính năng nâng cao như truyền tải theo mảnh (chunk), mã hóa AES và khử trùng lặp dữ liệu (deduplication).

## ✨ Tính năng nổi bật

- **Hỗ trợ tệp tin lớn**: Tự động chia tệp thành các mảnh nhỏ (lên đến 100MB mỗi mảnh) để truyền tải các tệp lớn hơn 2GB một cách ổn định.
- **Tiếp tục khi bị gián đoạn (Resume)**: Các tiến trình tải lên/tải xuống bị ngắt quãng có thể tiếp tục từ mảnh thành công cuối cùng.
- **Khử trùng lặp (Deduplication)**: Nhận diện các tệp nội dung giống nhau để chỉ lưu trữ một lần duy nhất, giúp tiết kiệm băng thông.
- **Mã hóa AES-GCM**: Mỗi mảnh dữ liệu được mã hóa bằng một mã nonce duy nhất và khóa 256-bit riêng biệt cho từng tệp.
- **Giao diện CLI đẹp mắt**: Hỗ trợ cả chế độ dòng lệnh trực tiếp và shell tương tác với các bảng biểu và sơ đồ cây trực quan nhờ thư viện `rich`.
- **Dễ dàng cài đặt**: Wrapper Node.js giúp cài đặt qua NPM nhanh chóng và tự động quản lý môi trường ảo Python.

## 📦 Cài đặt

Yêu cầu hệ thống: **Python 3.8+** và **Node.js**.

```bash
npm install -g telefs
```

## 🚀 Hướng dẫn sử dụng

### 1. Cấu hình ban đầu
Trong lần chạy đầu tiên, bạn cần cung cấp `API_ID` và `API_HASH` của Telegram (lấy tại [my.telegram.org](https://my.telegram.org)).

```bash
telefs
```

### 2. Các lệnh thông dụng

**Liệt kê tệp tin:**
```bash
telefs ls /Documents
```

**Tải tệp lên:**
```bash
telefs upload ~/Movies/phim_hay.mp4 /Videos
```

**Tải tệp về:**
```bash
telefs download /Videos/phim_hay.mp4 ./downloads/
```

**Xem sơ đồ cây thư mục:**
```bash
telefs tree
```

**Xóa tệp/thư mục:**
```bash
telefs rm -r /OldFolder
```

## 🛠 Chế độ Shell tương tác

TeleFS cung cấp một shell tương tác để bạn quản lý tệp như trên máy tính:

```bash
telefs
telefs: /> cd /Photos
telefs: /Photos> ls
telefs: /Photos> ul anh_meo.png
telefs: /Photos> quit
```

## 🔒 Bảo mật
- **Metadata cục bộ**: Thông tin về tệp và khóa mã hóa được lưu trữ trong cơ sở dữ liệu SQLite cục bộ (`~/.config/telefs/metadata.db`).
- **Mã hóa đầu cuối**: Dữ liệu luôn được mã hóa trên máy của bạn trước khi gửi lên Telegram.

## 📄 Giấy phép
MIT
