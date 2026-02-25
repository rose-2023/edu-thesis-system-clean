"""
檢查 MongoDB 中的數據
執行方法: python check_db.py
"""
from pymongo import MongoClient

client = MongoClient("mongodb://127.0.0.1:27017")
db = client["thesis_system"]

print("=== MongoDB 數據檢查 ===\n")

# 檢查 users
print("【Users 集合】")
users_count = db.users.count_documents({})
print(f"總數：{users_count} 個")
for user in db.users.find():
    print(f"  - {user.get('student_id')}: {user.get('name')} (role: {user.get('role')})")

print("\n【Videos 集合】")
videos_count = db.videos.count_documents({})
print(f"總數：{videos_count} 個")
if videos_count > 0:
    for video in db.videos.find():
        print(f"  - {video.get('title')} (unit: {video.get('unit')})")
        print(f"    ID: {video.get('_id')}")
        print(f"    文件: {video.get('filename')}")
else:
    print("  沒有影片資料")

# 詳細檢查所有集合
print("\n【所有集合】")
for collection_name in db.list_collection_names():
    count = db[collection_name].count_documents({})
    print(f"  - {collection_name}: {count} 筆")
