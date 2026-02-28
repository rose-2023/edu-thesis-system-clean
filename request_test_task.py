import requests

res = requests.get('http://127.0.0.1:5000/api/parsons/test/task',
                   params={'student_id':'11461127','test_role':'pre','test_cycle_id':'default'})
print(res.status_code, res.text)
