from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

app = FastAPI()

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    token = ws.cookies.get("pivot_token")
    await ws.send_text(f"Token is {token}")
    await ws.close()

client = TestClient(app)
with client.websocket_connect("/ws", cookies={"pivot_token": "secret"}) as websocket:
    data = websocket.receive_text()
    print(data)
