"""
V1.7 遷移腳本：為資料庫添加回看追蹤功能
執行方法: python app/scripts/migrate_v17_rewatch.py
"""
from pymongo import MongoClient
from datetime import datetime
import uuid

client = MongoClient("mongodb://127.0.0.1:27017")
db = client["thesis_system"]

print("=" * 60)
print("🚀 V1.7 數據庫遷移：回看追蹤功能")
print("=" * 60)

# === 1. 為現有用戶添加新欄位 ===
print("\n📝 Step 1: 更新 users 集合...")
users_count = db.users.count_documents({})
print(f"   - 找到 {users_count} 個用戶")

# 添加 participant_id（如果沒有）
result = db.users.update_many(
    {"participant_id": {"$exists": False}},
    [{
        "$set": {
            "participant_id": {"$strLn": {"$toString": "$_id"}}[:8]  # 簡化方式
        }
    }]
)
print(f"   - 已為 {result.modified_count} 個用戶添加 participant_id")

# 添加時間戳和 rewatch_stats（如果沒有）
result = db.users.update_many(
    {"created_at": {"$exists": False}},
    [{
        "$set": {
            "created_at": datetime.utcnow().isoformat(),
            "last_login_at": None,
            "rewatch_stats": {
                "total_rewatch_count": 0,
                "videos_never_rewatched": [],
                "rewatch_sessions": []
            }
        }
    }]
)
print(f"   - 已為 {result.modified_count} 個用戶添加時間戳和 rewatch_stats")

# 對於已有的用戶，補充缺失的欄位
result = db.users.update_many(
    {"rewatch_stats": {"$exists": False}},
    [{
        "$set": {
            "rewatch_stats": {
                "total_rewatch_count": 0,
                "videos_never_rewatched": [],
                "rewatch_sessions": []
            }
        }
    }]
)
print(f"   - 已補充 {result.modified_count} 個用戶的 rewatch_stats")

# === 2. 為 parsons_attempts 添加回看日誌參考 ===
print("\n📝 Step 2: 更新 parsons_attempts 集合...")
attempts_count = db.parsons_attempts.count_documents({})
print(f"   - 找到 {attempts_count} 條練習記錄")

result = db.parsons_attempts.update_many(
    {"review_log_id": {"$exists": False}},
    [{
        "$set": {
            "review_log_id": None,
            "review_log_recorded_at": None
        }
    }]
)
print(f"   - 已為 {result.modified_count} 條練習添加回看日誌欄位")

# === 3. 創建視頻回看日誌集合（如果不存在） ===
print("\n📝 Step 3: 確保 video_rewatch_logs 集合存在...")
if "video_rewatch_logs" not in db.list_collection_names():
    db.create_collection("video_rewatch_logs")
    print("   - ✅ 已創建 video_rewatch_logs 集合")
else:
    print("   - ✓ video_rewatch_logs 已存在")

# === 4. 創建索引以加快查詢 ===
print("\n📝 Step 4: 創建查詢索引...")
try:
    # users 表索引
    db.users.create_index("student_id", unique=True)
    db.users.create_index("participant_id")
    db.users.create_index("class_name")
    print("   ✓ users 索引已創建")
except:
    print("   ⚠ users 索引已存在或出現問題")

try:
    # video_rewatch_logs 索引
    db.video_rewatch_logs.create_index("student_id")
    db.video_rewatch_logs.create_index("participant_id")
    db.video_rewatch_logs.create_index("attempt_id")
    db.video_rewatch_logs.create_index("video_id")
    db.video_rewatch_logs.create_index("task_id")
    db.video_rewatch_logs.create_index([("student_id", 1), ("recorded_at", -1)])
    db.video_rewatch_logs.create_index([("video_id", 1), ("recorded_at", -1)])
    print("   ✓ video_rewatch_logs 索引已創建")
except:
    print("   ⚠ video_rewatch_logs 索引已存在或出現問題")

try:
    # parsons_attempts 索引
    db.parsons_attempts.create_index("student_id")
    db.parsons_attempts.create_index("review_log_id")
    print("   ✓ parsons_attempts 索引已更新")
except:
    print("   ⚠ parsons_attempts 索引已存在或出現問題")

# === 5. 驗證遷移結果 ===
print("\n✅ Step 5: 驗證遷移結果...")
print(f"   - users: {db.users.count_documents({})} 筆")
print(f"   - video_rewatch_logs: {db.video_rewatch_logs.count_documents({})} 筆")
print(f"   - parsons_attempts (帶 student_id): {db.parsons_attempts.count_documents({'student_id': {'$exists': True}})} 筆")

print("\n" + "=" * 60)
print("✅ V1.7 遷移完成！")
print("=" * 60)
print("\n📊 現在可以开始記錄以下數據：")
print("  1. ✅ 學生視頻回看次數")
print("  2. ✅ 每個學生的回看時長")
print("  3. ✅ 是否看完整個片段 (reached_end)")
print("  4. ✅ Seek 行為追蹤（頻繁 seek 檢測）")
print("  5. ✅ 回看後是否答對 (followup)")
print("\n📈 分析 API 已可用：")
print("  - GET /api/records/rewatch_stats?student_id=...")
print("  - GET /api/records/class_rewatch_analytics?class_name=...")
print("  - GET /api/records/rewatch_behavior_summary?video_id=...")
print("\n")
