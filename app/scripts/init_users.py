"""
初始化測試用戶
執行方法: python app/scripts/init_users.py
"""
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

client = MongoClient("mongodb://127.0.0.1:27017")
db = client["thesis_system"]

# 清空現有用戶（可選）
# db.users.delete_many({})

# 創建測試用戶
test_users = [
    {
        "student_id": "11461127",
        "name": "測試學生1",
        "class_name": "資工系 A班",
        "role": "student",
        "password_hash": generate_password_hash("123456")
    },
    {
        "student_id": "admin",
        "name": "老師",
        "class_name": "管理員",
        "role": "admin",
        "password_hash": generate_password_hash("admin123")
    },
    {
        "student_id": "A123456789",
        "name": "測試學生2",
        "class_name": "資工系 B班",
        "role": "student",
        "password_hash": generate_password_hash("password123")
    }
]

# 插入用戶（先檢查是否存在）
for user_data in test_users:
    existing = db.users.find_one({"student_id": user_data["student_id"]})
    if existing:
        print(f"⚠️ 用戶 {user_data['student_id']} 已存在，跳過")
    else:
        result = db.users.insert_one(user_data)
        print(f"✅ 已創建用戶 {user_data['student_id']} - {user_data['name']}")

print("\n=== 測試用戶登入資訊 ===")
print("學號: 11461127, 密碼: 123456 (角色: student)")
print("學號: admin, 密碼: admin123 (角色: admin/老師)")
print("學號: A123456789, 密碼: password123 (角色: student)")
