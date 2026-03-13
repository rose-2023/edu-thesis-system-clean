from app import create_app
app = create_app()
with app.app_context():
    db = app.config.get("db")
    doc = db.parsons_tasks.find_one({})
    import pprint
    pprint.pprint(doc)
