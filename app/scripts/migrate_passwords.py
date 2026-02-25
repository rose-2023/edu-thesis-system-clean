from pymongo import MongoClient
from werkzeug.security import generate_password_hash

client = MongoClient("mongodb://127.0.0.1:27017")
db = client["thesis_system"]

users = db.users.find({
    "password": {"$exists": True}, 
    "password_hash": {"$exists": False}
    
})

count = 0
for u in users:
    plain = u.get("password")
    if not plain:
        continue

    db.users.update_one(
        {"_id": u["_id"]},
        {"$set": {"password_hash": generate_password_hash(plain)},
         "$unset": {"password": ""}}
    )
    count += 1

print(f"✅ 已轉換完成：{count} 筆使用者")
