from dotenv import load_dotenv
load_dotenv()
from app import create_app
app = create_app()

print("=== ROUTES CONTAIN test/submit ===")
for r in app.url_map.iter_rules():
    if "test/submit" in str(r.rule):
        print(r, r.methods)
print("=== END ===")

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True, use_reloader=True)

