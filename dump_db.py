from pymongo import MongoClient

client = MongoClient('mongodb://localhost:27017')
db = client['thesis_system']
doc = db.parsons_tasks.find_one({})
import pprint
pprint.pprint(doc)
