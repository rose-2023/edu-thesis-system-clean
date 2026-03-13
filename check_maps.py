from pymongo import MongoClient
client = MongoClient('mongodb://localhost:27017')
db = client['thesis_system']
for doc in db.parsons_tasks.find({}):
    print('id', doc.get('_id'))
    print('subtitle_range', doc.get('subtitle_range'))
    print('ai_segment_map', doc.get('ai_segment_map'))
    print('ai_segments_compact', doc.get('ai_segments_compact'))
    print('---')
