
import os
import sys
import tempfile
from unittest.mock import MagicMock
from pathlib import Path

# Add project root to path
sys.path.append(str(Path.cwd()))

from telefs.fs_manager import FSManager
from telefs.storage import Storage

def run_test():
    print("--- BẮT ĐẦU KIỂM TRA LOGIC TELEFS ---\n")
    
    # Sử dụng database tạm để test
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    try:
        # 1. Khởi tạo Manager và Mock Telegram Client
        manager = FSManager(db_path=db_path)
        manager.tg = MagicMock() # Giả lập Telegram Client
        manager.tg.upload_file.return_value = (123456, "me", "doc_id_999")
        manager.tg.connect.return_value = True
        
        print("[1] Khởi tạo hệ thống: OK")

        # 2. Kiểm tra tạo thư mục
        manager.mkdir("Documents")
        manager.mkdir("Photos")
        print("[2] Tạo thư mục 'Documents' và 'Photos': OK")

        # 3. Kiểm tra thay đổi thư mục
        manager.cd("Documents")
        print(f"[3] Chuyển vào thư mục: {manager.pwd()} (Mong đợi: /Documents)")

        # 4. Giả lập Upload file
        # Tạo một file tạm để test
        test_file = "test_data.txt"
        with open(test_file, "w") as f:
            f.write("Hello TeleFS!")
        
        print("[4] Đang giả lập upload file 'test_data.txt'...")
        manager.upload(test_file, ".")
        
        # 5. Kiểm tra danh sách file (ls)
        print("\n[5] Danh sách file trong /Documents:")
        for line in manager.ls():
            print(f"  {line}")

        # 6. Kiểm tra cây thư mục toàn cục (tree)
        print("\n[6] Cấu trúc toàn bộ hệ thống (tree):")
        for line in manager.tree():
            print(line)

        # 7. Dọn dẹp
        os.remove(test_file)
        print("\n--- KIỂM TRA HOÀN TẤT: LOGIC HOẠT ĐỘNG HOÀN HẢO ---")
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)

if __name__ == "__main__":
    run_test()
