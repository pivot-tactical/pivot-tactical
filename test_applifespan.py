import asyncio
from fastapi import FastAPI
from server.pivot.config import Settings
from server.pivot.api.app import AppLifespan

def test():
    app = FastAPI()
    cfg = Settings()
    lifespan = AppLifespan(cfg)
    print("Lifespan manager created successfully.")

test()
