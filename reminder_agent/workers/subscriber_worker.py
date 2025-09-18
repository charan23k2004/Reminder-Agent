# workers/subscriber_worker.py
import redis, json
r = redis.from_url("redis://localhost:6379/0")
ps = r.pubsub()
ps.subscribe("reminder_notifications")
print("Subscribed, waiting...")
for msg in ps.listen():
    if msg['type'] != 'message': continue
    payload = json.loads(msg['data'])
    print("EVENT:", payload)
    # Example: if payload['type']=="reminder.fired": call external push service, log analytics, etc.
