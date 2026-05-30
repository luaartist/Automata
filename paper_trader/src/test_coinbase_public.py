import asyncio
import json
import websockets

async def test():
    url = "wss://ws-feed.exchange.coinbase.com"
    async with websockets.connect(url) as websocket:
        subscribe_msg = {
            "type": "subscribe",
            "product_ids": ["BTC-USD"],
            "channels": ["level2"]
        }
        await websocket.send(json.dumps(subscribe_msg))
        print("Sent subscription request")
        for _ in range(10):
            response = await websocket.recv()
            print("Received:", response[:500])

asyncio.run(test())
