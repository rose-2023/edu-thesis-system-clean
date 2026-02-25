from flask import Blueprint, request, jsonify
from werkzeug.security import check_password_hash
from ..db import db

auth_bp = Blueprint("auth", __name__)

@auth_bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    student_id = (data.get("student_id") or data.get("studentId") or "").strip()
    password = (data.get("password") or "").strip()

    print(f"[DEBUG] 登入嘗試 - 學號: {student_id}, 密碼長度: {len(password)}")

    if not student_id or not password:
        return jsonify({"ok": False, "message": "請輸入學號與密碼"}), 400

    user = db.users.find_one({"student_id": student_id})
    print(f"[DEBUG] 查詢用戶: {user is not None}")

    if not user:
        print(f"[DEBUG] 用戶 {student_id} 不存在")
        return jsonify({"ok": False, "message": "帳號或密碼錯誤"}), 401

    stored_hash = user.get("password_hash")
    if not stored_hash:
        print(f"[DEBUG] 用戶 {student_id} 無密碼雜湊")
        return jsonify({"ok": False, "message": "此帳號尚未設定密碼，請聯絡管理者"}), 400

    if not check_password_hash(stored_hash, password):
        print(f"[DEBUG] 用戶 {student_id} 密碼錯誤")
        return jsonify({"ok": False, "message": "帳號或密碼錯誤"}), 401

    print(f"[DEBUG] 用戶 {student_id} 登入成功")

    uid = str(user.get("_id"))
    return jsonify({
        "ok": True,
        "token": uid,                 # ✅ 給前端 router guard 用
        "participant_id": uid,         # ✅ 你系統原本也用這個
        "name": user.get("name", ""),
        "class_name": user.get("class_name", ""),
        "role": user.get("role", "student")
    })
