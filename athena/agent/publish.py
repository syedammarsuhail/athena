import asyncio, nats, json

async def pub():
    nc = await nats.connect('nats://nats:4222')
    payload = {
        'kind': 'metric_anomaly',
        'service': 'cartservice',
        'namespace': 'online-boutique',
        'metric': 'container_memory_working_set_bytes',
        'score': 0.97,
        'severity': 'critical',
        'ts': 1747602000.0,
        'raw': 'memory at 814MB'
    }
    await nc.publish('anomalies.metric', json.dumps(payload).encode())
    await nc.drain()
    print('event published!')

asyncio.run(pub())
