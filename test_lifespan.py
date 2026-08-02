from fastapi import FastAPI
from contextlib import asynccontextmanager

class LifespanManager:
    def __init__(self, name):
        self.name = name

    @asynccontextmanager
    async def __call__(self, app: FastAPI):
        app.state.name = self.name
        yield
        app.state.name = None

app = FastAPI(lifespan=LifespanManager("test"))

if __name__ == "__main__":
    import asyncio
    async def test():
        async with app.router.lifespan_context(app):
            print("Name:", app.state.name)
    asyncio.run(test())
