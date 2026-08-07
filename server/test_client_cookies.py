from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

app = FastAPI()


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    await ws.send_text(ws.cookies.get("pivot_token", "None"))
    await ws.close()


client = TestClient(app)
with client.websocket_connect("/ws", cookies={"pivot_token": "secret_token"}) as ws:
    data = ws.receive_text()
    print("Received:", data)
