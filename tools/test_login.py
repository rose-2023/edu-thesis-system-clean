import json
import urllib.request
import urllib.error

URL = "http://127.0.0.1:5000/api/auth/login"
payload = {"student_id": "admin", "password": "admin123"}
data = json.dumps(payload).encode('utf-8')
req = urllib.request.Request(URL, data=data, headers={"Content-Type": "application/json"}, method='POST')

try:
    with urllib.request.urlopen(req, timeout=5) as resp:
        body = resp.read().decode('utf-8')
        print('STATUS', resp.status)
        print('BODY')
        print(body)
except urllib.error.HTTPError as e:
    print('HTTP ERROR', e.code)
    try:
        print(e.read().decode())
    except Exception:
        pass
except Exception as e:
    print('ERROR', str(e))
