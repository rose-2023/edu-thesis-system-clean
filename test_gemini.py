import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

response = client.models.generate_content(
    model=os.getenv("GEMINI_MODEL", "gemini-3-flash-preview"),
    contents="請幫我出一題 Python for 迴圈初學者題目，回傳 JSON。"
)

print(response.text)