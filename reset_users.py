"""
重新初始化所有測試用戶（清除舊的）
執行方法: python reset_users.py
"""
from pymongo import MongoClient
from werkzeug.security import generate_password_hash
from datetime import datetime
import uuid

client = MongoClient("mongodb://127.0.0.1:27017")
db = client["thesis_system"]

# 清空所有現有用戶
print("🧹 清除舊用戶...")
db.users.delete_many({})

# 創建新的測試用戶
test_users = [
    {
        "student_id": "admin",
        "name": "老師",
        "class_name": "管理員",
        "role": "admin",
        "password_hash": generate_password_hash("admin123"),
        "participant_id": str(uuid.uuid4()),  # 研究匿名編號
        "created_at": datetime.utcnow().isoformat(),
        "last_login_at": None,
        "rewatch_stats": {  # V1.7: 回看統計
            "total_rewatch_count": 0,
            "videos_never_rewatched": [],
            "rewatch_sessions": []
        }
    },
    {
        "student_id": "11461127",
        "name": "測試學生1",
        "class_name": "資工系 A班",
        "role": "student",
        "password_hash": generate_password_hash("123456"),
        "participant_id": str(uuid.uuid4()),
        "created_at": datetime.utcnow().isoformat(),
        "last_login_at": None,
        "rewatch_stats": {
            "total_rewatch_count": 0,
            "videos_never_rewatched": [],
            "rewatch_sessions": []
        }
    },
    {
        "student_id": "A123456789",
        "name": "測試學生2",
        "class_name": "資工系 B班",
        "role": "student",
        "password_hash": generate_password_hash("password123"),
        "participant_id": str(uuid.uuid4()),
        "created_at": datetime.utcnow().isoformat(),
        "last_login_at": None,
        "rewatch_stats": {
            "total_rewatch_count": 0,
            "videos_never_rewatched": [],
            "rewatch_sessions": []
        }
    }
]

# 插入用戶
for user_data in test_users:
    result = db.users.insert_one(user_data)
    print(f"✅ 已建立用戶: {user_data['student_id']} ({user_data['name']})")
    print(f"   └─ participant_id: {user_data['participant_id']}")

print("\n" + "="*50)
print("📋 測試用戶登入資訊")
print("="*50)
print("👨‍🏫 老師帳號:")
print("  學號: admin")
print("  密碼: admin123")
print("\n👨‍🎓 學生帳號:")
print("  學號: 11461127")
print("  密碼: 123456")
print("\n👨‍🎓 學生帳號 2:")
print("  學號: A123456789")
print("  密碼: password123")
print("="*50 + "\n")

# 驗證
print("🔍 驗證用戶...")
users = list(db.users.find({}, {"student_id": 1, "name": 1, "role": 1, "participant_id": 1}))
for u in users:
    print(f"  - {u['student_id']}: {u['name']} ({u['role']})")
    print(f"    participant_id: {u.get('participant_id', 'N/A')}")

