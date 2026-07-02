import os

from pymongo import MongoClient


client = MongoClient(
    os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017"),
    maxPoolSize=max(20, int(os.environ.get("MONGO_MAX_POOL_SIZE", "100"))),
    waitQueueTimeoutMS=int(os.environ.get("MONGO_WAIT_QUEUE_TIMEOUT_MS", "5000")),
    serverSelectionTimeoutMS=int(os.environ.get("MONGO_SERVER_TIMEOUT_MS", "5000")),
    connectTimeoutMS=int(os.environ.get("MONGO_CONNECT_TIMEOUT_MS", "5000")),
    retryWrites=True,
)
db = client[os.environ.get("MONGO_DATABASE", "thesis_system")]
