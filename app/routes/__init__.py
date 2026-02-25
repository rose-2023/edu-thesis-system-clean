from .auth import auth_bp
from .student import student_bp
from .admin import admin_bp
from .admin_upload import admin_upload_bp
from .subtitle import subtitle_bp
from .teacher_dashboard import teacher_dashboard_bp
from .parsons_admin import parsons_admin_bp
from .records import records_bp
from .events import events_bp
from .teacher_t5 import teacher_t5_bp
from .parsons import parsons_bp

def register_blueprints(app):
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(student_bp, url_prefix="/api/student")
    app.register_blueprint(admin_bp, url_prefix="/api/admin")
    app.register_blueprint(admin_upload_bp, url_prefix="/api/admin_upload")
    # 新增
    app.register_blueprint(subtitle_bp, url_prefix="/api/subtitle")
    app.register_blueprint(teacher_dashboard_bp, url_prefix="/api/teacher_dashboard")
    app.register_blueprint(parsons_admin_bp, url_prefix="/api/parsons_admin")
    app.register_blueprint(records_bp, url_prefix="/api/records")
    app.register_blueprint(events_bp, url_prefix="/api/events")
    app.register_blueprint(teacher_t5_bp, url_prefix="/api/teacher_t5")
    app.register_blueprint(parsons_bp, url_prefix="/api/parsons")

