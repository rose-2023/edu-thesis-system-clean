from pymongo import MongoClient

# 本機開發：先不加帳密（上線前再加 auth）
client = MongoClient("mongodb://127.0.0.1:27017")
db = client["thesis_system"]
