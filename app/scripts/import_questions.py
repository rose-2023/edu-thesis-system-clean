import json
from datetime import datetime
from pymongo import MongoClient

MONGO_URI = "mongodb://127.0.0.1:27017"
DB_NAME = "thesis_system"
JSON_PATH = "app/scripts/pre_questions_u1.json"

def main():
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]

    # 讀取 JSON
    with open(JSON_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)

    if not isinstance(items, list) or len(items) == 0:
        print("⚠️ JSON 沒有題目或格式不是陣列")
        return

    docs = []
    for q in items:
        docs.append({
            "type": q["type"],               # "mcq"
            "unit": q["unit"],               # "U1"
            "difficulty": int(q.get("difficulty", 2)),
            "tags": q.get("tags", []),
            "stem": q["stem"],
            "options": q["options"],         # ["A..","B..","C..","D.."]
            "answer_key": q["answer_key"],   # "A"/"B"/"C"/"D" (⚠️ 前端不要拿到)
            "active": True,
            "created_at": datetime.utcnow()
        })

    # 寫入 MongoDB
    r = db.questions.insert_many(docs)
    print(f"✅ 匯入完成：{len(r.inserted_ids)} 題 -> {DB_NAME}.questions")

if __name__ == "__main__":
    main()
