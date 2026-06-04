from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

load_dotenv()

client = OpenAI()

# Define your response schema
class BedtimeStory(BaseModel):
    title: str
    story: str
    moral: str

response = client.chat.completions.create(
    model="gpt-4",
    messages=[
        {"role": "user", "content": "Write a one-sentence bedtime story about a unicorn."}
    ],
    response_format={
        "type": "json_schema",
        "json_schema": {
            "name": "BedtimeStory",
            "schema": BedtimeStory.model_json_schema(),
            "strict": True
        }
    }
)

import json
story = json.loads(response.choices[0].message.content)
print(f"Title: {story['title']}")
print(f"Story: {story['story']}")
print(f"Moral: {story['moral']}")