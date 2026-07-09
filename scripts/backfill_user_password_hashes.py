# 如果從MongoDB直接匯入 users 要先啟動這個檔案才會先建檔，如果有修改帳號、密碼都要先啟動這個檔案才會建檔，否則會出現錯誤。
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, PyMongoError, ServerSelectionTimeoutError
from werkzeug.security import generate_password_hash


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
MONGO_DATABASE = os.environ.get("MONGO_DATABASE", "thesis_system")

MISSING_PASSWORD_HASH = {
    "$or": [
        {"password_hash": {"$exists": False}},
        {"password_hash": None},
        {"password_hash": ""},
    ],
}


def main():
    client = MongoClient(
        MONGO_URI,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
    )
    try:
        client.admin.command("ping")
        users = client[MONGO_DATABASE].users
        query = {
            "role": "student",
            "student_id": {"$type": "string", "$ne": ""},
            **MISSING_PASSWORD_HASH,
        }
        candidates = list(
            users.find(
                query,
                {
                    "_id": 1,
                    "student_id": 1,
                    "created_at": 1,
                    "last_login_at": 1,
                },
            )
        )

        updated_count = 0
        for user in candidates:
            student_id = str(user.get("student_id") or "").strip()
            if not student_id:
                continue

            now = datetime.now(timezone.utc)
            update_fields = {
                "password_hash": generate_password_hash(student_id),
                "updated_at": now,
            }
            if "created_at" not in user:
                update_fields["created_at"] = now
            if "last_login_at" not in user:
                update_fields["last_login_at"] = None

            result = users.update_one(
                {"_id": user["_id"], **MISSING_PASSWORD_HASH},
                {"$set": update_fields},
            )
            updated_count += result.modified_count

        print(f"找到 {len(candidates)} 筆缺少 password_hash 的學生資料。")
        print(f"成功補建 {updated_count} 筆 password_hash。")
        print("既有 password_hash 未被修改，資料庫未儲存明文密碼。")
        return 0
    except (ServerSelectionTimeoutError, ConnectionFailure):
        print("MongoDB 連線失敗，請確認 MongoDB 是否已啟動並監聽 127.0.0.1:27017。")
        return 1
    except PyMongoError as exc:
        print(f"補建 password_hash 失敗：{exc}")
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
