import caldav, os
from dotenv import load_dotenv
load_dotenv()
client = caldav.DAVClient(
    url='https://caldav.icloud.com',
    username=os.getenv('ICLOUD_USERNAME'),
    password=os.getenv('ICLOUD_APP_PASSWORD'),
)
for c in client.principal().calendars():
    print(f'{c.name} -> {c.url}')
