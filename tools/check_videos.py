#!/usr/bin/env python3
import os
from pymongo import MongoClient

print("\n=== 📹 影片診斷 ===\n")

try:
    client = MongoClient("mongodb://localhost:27017/")
    db = client["thesis_system"]
    
    videos = list(db.videos.find({"deleted": {"$ne": True}}))
    print(f"📊 數據庫中有 {len(videos)} 部影片\n")
    
    if videos:
        for i, v in enumerate(videos[:3], 1):
            print(f"--- 影片 {i} ---")
            print(f"ID: {v.get('_id')}")
            print(f"標題: {v.get('title')}")
            print(f"路徑: {v.get('path')}")
            
            if v.get('path'):
                abs_path = os.path.join(os.getcwd(), v['path'])
                exists = os.path.exists(abs_path)
                print(f"完整路徑: {abs_path}")
                print(f"檔案存在: {exists}")
                if exists:
                    size = os.path.getsize(abs_path)
                    print(f"大小: {size / (1024*1024):.2f} MB")
            print()
    else:
        print("❌ 數據庫中沒有影片！\n")
    
except Exception as e:
    print(f"❌ MongoDB 連接失敗: {e}\n")

print("=== 📁 上傳目錄檢查 ===\n")
uploads_dir = os.path.join(os.getcwd(), "uploads")
for subdir in ["videos", "thumbnails", "subtitles"]:
    path = os.path.join(uploads_dir, subdir)
    if os.path.exists(path):
        files = os.listdir(path)
        print(f"✅ {subdir}/  ({len(files)} 個檔案)")
    else:
        print(f"❌ {subdir}/ 不存在")
