"""
V1.7 系統驗證腳本
檢查資料庫、API 和前端是否正確配置

執行方法: python test_v17_rewatch.py
"""
import requests
import json
from pymongo import MongoClient
from datetime import datetime
import time

# ============= 配置 =============
MONGO_URI = "mongodb://127.0.0.1:27017"
API_BASE = "http://127.0.0.1:5000"
DB_NAME = "thesis_system"

# ============= 測試函數 =============

def test_mongodb_connection():
    """測試 MongoDB 連接"""
    print("\n🔍 [1/5] 測試 MongoDB 連接...")
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        client.admin.command('ping')
        db = client[DB_NAME]
        print("  ✅ MongoDB 連接成功")
        return db
    except Exception as e:
        print(f"  ❌ MongoDB 連接失敗: {e}")
        return None

def test_database_schema(db):
    """測試資料庫架構"""
    print("\n🔍 [2/5] 驗證資料庫架構...")
    
    # 檢查集合
    collections = db.list_collection_names()
    print(f"  找到 {len(collections)} 個集合:")
    
    required = ["users", "video_rewatch_logs", "parsons_attempts"]
    for col in required:
        if col in collections:
            count = db[col].count_documents({})
            print(f"    ✅ {col}: {count} 筆")
        else:
            print(f"    ❌ {col}: 不存在")
    
    # 檢查用戶欄位
    print("\n  檢查 users 表欄位:")
    sample_user = db.users.find_one({})
    if sample_user:
        required_fields = ["participant_id", "created_at", "rewatch_stats"]
        for field in required_fields:
            if field in sample_user:
                print(f"    ✅ {field}: 存在")
            else:
                print(f"    ⚠️  {field}: 缺失")
    else:
        print("    ⚠️  users 表為空")
    
    # 檢查索引
    print("\n  檢查索引:")
    try:
        indexes = db.video_rewatch_logs.list_indexes()
        print(f"    ✅ video_rewatch_logs 有 {len(list(indexes))} 個索引")
    except:
        print("    ⚠️  無法列出索引")

def test_api_endpoints():
    """測試 API 端點"""
    print("\n🔍 [3/5] 測試 API 端點...")
    
    # 測試 review_watch 端點
    print("  測試 POST /api/parsons/review_watch:")
    try:
        response = requests.post(
            f"{API_BASE}/api/parsons/review_watch",
            json={
                "attempt_id": "test_id_123",
                "video_id": "video_test",
                "watch_seconds": 120,
                "reached_end": False,
                "seek_events": []
            },
            timeout=5
        )
        if response.status_code in [200, 400, 404]:  # 任何響應都表示端點存在
            print(f"    ✅ 端點響應 (Status: {response.status_code})")
        else:
            print(f"    ⚠️  異常狀態: {response.status_code}")
    except requests.exceptions.ConnectionError:
        print(f"    ❌ 無法連接 {API_BASE}")
    except Exception as e:
        print(f"    ⚠️  錯誤: {e}")
    
    # 測試查詢端點
    print("  測試 GET /api/records/rewatch_stats:")
    try:
        response = requests.get(
            f"{API_BASE}/api/records/rewatch_stats?student_id=11461127",
            timeout=5
        )
        if response.status_code == 200:
            print(f"    ✅ 端點響應成功")
        else:
            print(f"    ⚠️  狀態: {response.status_code}")
    except Exception as e:
        print(f"    ⚠️  錯誤: {e}")

def test_data_insertion(db):
    """測試數據插入"""
    print("\n🔍 [4/5] 測試數據插入...")
    
    # 插入測試回看日誌
    test_log = {
        "attempt_id": f"test_{int(time.time())}",
        "video_id": "test_video_001",
        "task_id": "test_task_001",
        "student_id": "11461127",
        "participant_id": "test_participant",
        "watch_seconds": 120,
        "reached_end": True,
        "watch_start_at": datetime.utcnow().isoformat(),
        "watch_end_at": datetime.utcnow().isoformat(),
        "seek_count": 2,
        "seek_events": [
            {"from": 10, "to": 30, "distance": 20},
            {"from": 40, "to": 45, "distance": 5}
        ],
        "is_frequent_seeker": False,
        "has_followup": False,
        "recorded_at": datetime.utcnow().isoformat()
    }
    
    try:
        result = db.video_rewatch_logs.insert_one(test_log)
        print(f"  ✅ 成功插入測試日誌: {result.inserted_id}")
        
        # 驗證插入
        retrieved = db.video_rewatch_logs.find_one({"_id": result.inserted_id})
        if retrieved:
            print(f"  ✅ 驗證: 數據已保存並可檢索")
        
        # 清理測試數據
        db.video_rewatch_logs.delete_one({"_id": result.inserted_id})
        print(f"  ℹ️  測試數據已清理")
    except Exception as e:
        print(f"  ❌ 插入失敗: {e}")

def test_frontend_integration():
    """測試前端集成"""
    print("\n🔍 [5/5] 檢查前端集成...")
    
    print("  檢查 StudentLearning.vue:")
    try:
        with open("frontend/src/pages/StudentLearning.vue", "r", encoding="utf-8") as f:
            content = f.read()
            
        checklist = {
            "seekEvents": "seekEvents" in content,
            "seek_count": "seek_count" in content,
            "seek_events": "seek_events:" in content,
            "reached_end": "reached_end" in content,
            "watch_seconds": "watch_seconds" in content
        }
        
        for feature, exists in checklist.items():
            status = "✅" if exists else "❌"
            print(f"    {status} {feature}")
    except FileNotFoundError:
        print("    ⚠️  StudentLearning.vue 未找到")
    except Exception as e:
        print(f"    ⚠️  檢查失敗: {e}")

# ============= 主函數 =============

def main():
    print("=" * 70)
    print("🧪 V1.7 視頻回看系統驗證")
    print("=" * 70)
    
    # 1. 測試 MongoDB
    db = test_mongodb_connection()
    if not db:
        print("\n❌ 無法繼續（MongoDB 不可用）")
        return
    
    # 2. 驗證架構
    test_database_schema(db)
    
    # 3. 測試 API
    test_api_endpoints()
    
    # 4. 測試插入
    test_data_insertion(db)
    
    # 5. 檢查前端
    test_frontend_integration()
    
    print("\n" + "=" * 70)
    print("✅ 驗證完成！")
    print("=" * 70)
    print("\n📋 檢查清單:")
    print("  □ MongoDB 已連接並有數據")
    print("  □ video_rewatch_logs 集合已創建")
    print("  □ API 端點已可用")
    print("  □ 前端已更新以追蹤 seek 事件")
    print("  □ 可以開始記錄學生回看數據")
    print("\n🚀 下一步:")
    print("  1. python reset_users.py  (重新初始化用戶)")
    print("  2. python app/scripts/migrate_v17_rewatch.py  (執行遷移)")
    print("  3. 訪問系統並測試回看功能")
    print("  4. python tools/analyze_rewatch.py --summary  (查看統計)")
    print("\n")

if __name__ == "__main__":
    main()
