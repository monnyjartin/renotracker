from datetime import date
from starlette.types import ASGIApp, Receive, Scope, Send

class InjectTodayMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http":
            scope.setdefault("state", {})
        await self.app(scope, receive, send)
