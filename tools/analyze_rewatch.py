"""
V1.7 回看行為分析工具
用於快速查詢和生成回看統計報告

執行方法:
  python tools/analyze_rewatch.py --student_id 11461127
  python tools/analyze_rewatch.py --class_name "資工系 A班"
  python tools/analyze_rewatch.py --summary
"""
from pymongo import MongoClient
from datetime import datetime
import sys
import argparse

client = MongoClient("mongodb://127.0.0.1:27017")
db = client["thesis_system"]

def analyze_student(student_id):
    """分析單個學生的回看行為"""
    print(f"\n{'='*70}")
    print(f"📊 學生回看分析: {student_id}")
    print(f"{'='*70}")
    
    user = db.users.find_one({"student_id": student_id})
    if not user:
        print(f"❌ 找不到學生: {student_id}")
        return
    
    print(f"姓名: {user.get('name')}")
    print(f"班級: {user.get('class_name')}")
    print(f"研究編號: {user.get('participant_id')}")
    
    # 查詢回看日誌
    logs = list(db.video_rewatch_logs.find({"student_id": student_id}).sort("recorded_at", -1))
    
    if not logs:
        print("❌ 此學生尚無回看記錄")
        return
    
    print(f"\n📈 統計數據:")
    print(f"  - 總回看次數: {len(logs)}")
    
    total_seconds = sum(log.get("watch_seconds", 0) for log in logs)
    print(f"  - 總觀看時長: {total_seconds}秒 ({round(total_seconds/60, 1)}分鐘)")
    
    completed = sum(1 for log in logs if log.get("reached_end"))
    print(f"  - 完整觀看次數: {completed}/{len(logs)} ({completed*100//len(logs)}%)")
    
    seekers = sum(1 for log in logs if log.get("is_frequent_seeker"))
    print(f"  - 頻繁 seek 次數: {seekers}")
    
    with_followup = sum(1 for log in logs if log.get("has_followup"))
    correct = sum(1 for log in logs if log.get("followup_is_correct"))
    print(f"  - 有回答的回看: {with_followup}")
    if with_followup > 0:
        print(f"  - 回答正確率: {correct}/{with_followup} ({correct*100//with_followup}%)")
    
    print(f"\n📋 詳細日誌:")
    for i, log in enumerate(logs, 1):
        print(f"\n  [{i}] 嘗試 {log.get('attempt_id')}")
        print(f"      視頻: {log.get('video_id')}")
        print(f"      觀看: {log.get('watch_seconds')}秒, 完整: {log.get('reached_end')}")
        seek_count = log.get('seek_count', 0)
        print(f"      Seek: {seek_count}次 {'(⚠️ 頻繁)' if log.get('is_frequent_seeker') else ''}")
        if log.get('has_followup'):
            correct_str = "✅ 正確" if log.get('followup_is_correct') else "❌ 錯誤"
            print(f"      回答: {correct_str}")
        print(f"      時間: {log.get('recorded_at')}")

def analyze_class(class_name):
    """分析整個班級的回看行為"""
    print(f"\n{'='*70}")
    print(f"📊 班級回看分析: {class_name}")
    print(f"{'='*70}")
    
    students = list(db.users.find(
        {"class_name": class_name, "role": "student"},
        {"student_id": 1, "name": 1}
    ))
    
    if not students:
        print(f"❌ 找不到班級: {class_name}")
        return
    
    print(f"班級成員數: {len(students)}\n")
    
    class_stats = []
    
    for student in students:
        sid = student.get("student_id")
        logs = list(db.video_rewatch_logs.find({"student_id": sid}))
        
        if logs:
            total_seconds = sum(log.get("watch_seconds", 0) for log in logs)
            completed = sum(1 for log in logs if log.get("reached_end"))
            seekers = sum(1 for log in logs if log.get("is_frequent_seeker"))
            correct = sum(1 for log in logs if log.get("followup_is_correct"))
            with_followup = sum(1 for log in logs if log.get("has_followup"))
            
            class_stats.append({
                "student_id": sid,
                "name": student.get("name"),
                "rewatch_count": len(logs),
                "total_minutes": round(total_seconds / 60, 1),
                "completion_rate": f"{completed*100//len(logs)}%",
                "frequent_seek": seekers > 0,
                "followup_accuracy": f"{correct*100//with_followup}%" if with_followup > 0 else "N/A"
            })
    
    # 按回看次數排序
    class_stats.sort(key=lambda x: x["rewatch_count"], reverse=True)
    
    print(f"{'學號':<15} {'姓名':<10} {'回看次':<8} {'時長':<8} {'完整率':<8} {'頻繁Seek':<8} {'回答正確率':<10}")
    print("-" * 70)
    
    for s in class_stats:
        seek_icon = "⚠️" if s["frequent_seek"] else "✓"
        print(f"{s['student_id']:<15} {s['name']:<10} {s['rewatch_count']:<8} "
              f"{s['total_minutes']:<8} {s['completion_rate']:<8} {seek_icon:<8} {s['followup_accuracy']:<10}")
    
    # 分類統計
    print(f"\n📊 班級分類:")
    never = sum(1 for s in class_stats if s["rewatch_count"] == 0)
    print(f"  - 從未回看: {never} 人")
    
    frequent_seekers = sum(1 for s in class_stats if s["frequent_seek"])
    print(f"  - 頻繁 seek: {frequent_seekers} 人")
    
    always_complete = sum(1 for s in class_stats if s["completion_rate"] == "100%")
    print(f"  - 完整觀看: {always_complete} 人")

def summary_all():
    """全系統總結統計"""
    print(f"\n{'='*70}")
    print(f"📊 全系統回看統計")
    print(f"{'='*70}")
    
    total_logs = db.video_rewatch_logs.count_documents({})
    print(f"\n📈 總體統計:")
    print(f"  - 總回看記錄: {total_logs} 筆")
    
    if total_logs > 0:
        logs = list(db.video_rewatch_logs.find({}))
        
        total_seconds = sum(log.get("watch_seconds", 0) for log in logs)
        completed = sum(1 for log in logs if log.get("reached_end"))
        seekers = sum(1 for log in logs if log.get("is_frequent_seeker"))
        
        print(f"  - 總觀看時長: {round(total_seconds/3600, 1)} 小時")
        print(f"  - 完整觀看率: {completed*100//total_logs}%")
        print(f"  - 包含頻繁 seek: {seekers} 筆")
    
    # 班級統計
    classes = db.users.distinct("class_name", {"role": "student"})
    print(f"\n📚 班級統計:")
    for cls in classes:
        students = db.users.count_documents({"class_name": cls, "role": "student"})
        logs = db.video_rewatch_logs.count_documents({"student_id": {"$in": [
            s["student_id"] for s in db.users.find({"class_name": cls}, {"student_id": 1})
        ]}})
        print(f"  - {cls}: {students} 人, {logs} 筆回看")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="V1.7 回看行為分析工具")
    parser.add_argument("--student_id", help="分析特定學生")
    parser.add_argument("--class_name", help="分析特定班級")
    parser.add_argument("--summary", action="store_true", help="顯示全系統概況")
    
    args = parser.parse_args()
    
    if args.student_id:
        analyze_student(args.student_id)
    elif args.class_name:
        analyze_class(args.class_name)
    elif args.summary:
        summary_all()
    else:
        parser.print_help()
