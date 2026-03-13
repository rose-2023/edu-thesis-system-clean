from pymongo import MongoClient
client = MongoClient('mongodb://localhost:27017')
db = client['thesis_system']
doc = db.parsons_tasks.find_one({})
from pprint import pprint
print('>>>> subtitle_range')
pprint(doc.get('subtitle_range'))
print('>>>> ai_segment_map')
pprint(doc.get('ai_segment_map'))
