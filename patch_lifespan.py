import re

with open('server/pivot/api/app.py', 'r') as f:
    content = f.read()

# Replace _create_app_lifespan with AppLifespan class
search_str = """def _create_app_lifespan(
    cfg: Settings, manager: SessionManager | None = None
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import asyncio

        if manager is not None:
            app.state.db = manager.db
            app.state.manager = manager
        else:
            db = init_database(cfg)
            app.state.db = db
            app.state.manager = SessionManager(db, cfg)
        app.state.settings = cfg
        # Record the running loop so cross-thread broadcasts marshal correctly.
        app.state.manager.loop = asyncio.get_running_loop()
        app.state.audio_router = None  # set by the audio plane when available

        # Instructor authentication: seed the default password on first run.
        app.state.auth = AuthService(app.state.manager.db)
        app.state.auth.ensure_default()

        # Reconcile any events stuck on "transcribing…" with no recording on
        # disk (e.g. logged before audio capture) so they show a terminal state.
        from pivot.db import repository as repo

        with app.state.manager.db.session() as s:
            repo.reconcile_orphan_transcriptions(s, cfg.recordings_dir)

        # Async transcription worker — only started if faster-whisper is present
        # (the live event log still works without it; transcripts stay pending).
        worker = _maybe_start_transcription(app.state.manager, cfg)
        app.state.transcription_worker = worker

        # Background update service: always-on async checks + session-gated
        # auto-update (§3.7). Broadcasts status changes to the instructor UI.
        update_service = _start_update_service(app.state.manager, cfg)
        app.state.update_service = update_service

        # Continuous ambient band noise ("hash") on tuned channels (§3.2.2):
        # a real-time task streams the per-frequency noise floor to idle
        # listeners so an open channel hisses until someone keys up. Optional
        # (PIVOT_AMBIENT_NOISE=0 for a silent-when-idle net).
        noise = None
        if getattr(cfg, "ambient_noise", True):
            from pivot.audio.noise_stream import NoiseBroadcaster

            noise = NoiseBroadcaster(app.state.manager)
            noise.start()
        app.state.noise_broadcaster = noise
        try:
            yield
        finally:
            if worker is not None:
                worker.stop()
            if update_service is not None:
                update_service.stop()
            if noise is not None:
                await noise.stop()

    return lifespan"""

replace_str = """class AppLifespan:
    \"\"\"Manages FastAPI application startup and shutdown lifecycle events.\"\"\"

    def __init__(self, cfg: Settings, manager: SessionManager | None = None):
        self.cfg = cfg
        self.manager = manager

    @asynccontextmanager
    async def __call__(self, app: FastAPI):
        import asyncio

        if self.manager is not None:
            app.state.db = self.manager.db
            app.state.manager = self.manager
        else:
            db = init_database(self.cfg)
            app.state.db = db
            app.state.manager = SessionManager(db, self.cfg)
        app.state.settings = self.cfg
        # Record the running loop so cross-thread broadcasts marshal correctly.
        app.state.manager.loop = asyncio.get_running_loop()
        app.state.audio_router = None  # set by the audio plane when available

        # Instructor authentication: seed the default password on first run.
        app.state.auth = AuthService(app.state.manager.db)
        app.state.auth.ensure_default()

        # Reconcile any events stuck on "transcribing…" with no recording on
        # disk (e.g. logged before audio capture) so they show a terminal state.
        from pivot.db import repository as repo

        with app.state.manager.db.session() as s:
            repo.reconcile_orphan_transcriptions(s, self.cfg.recordings_dir)

        # Async transcription worker — only started if faster-whisper is present
        # (the live event log still works without it; transcripts stay pending).
        worker = _maybe_start_transcription(app.state.manager, self.cfg)
        app.state.transcription_worker = worker

        # Background update service: always-on async checks + session-gated
        # auto-update (§3.7). Broadcasts status changes to the instructor UI.
        update_service = _start_update_service(app.state.manager, self.cfg)
        app.state.update_service = update_service

        # Continuous ambient band noise ("hash") on tuned channels (§3.2.2):
        # a real-time task streams the per-frequency noise floor to idle
        # listeners so an open channel hisses until someone keys up. Optional
        # (PIVOT_AMBIENT_NOISE=0 for a silent-when-idle net).
        noise = None
        if getattr(self.cfg, "ambient_noise", True):
            from pivot.audio.noise_stream import NoiseBroadcaster

            noise = NoiseBroadcaster(app.state.manager)
            noise.start()
        app.state.noise_broadcaster = noise
        try:
            yield
        finally:
            if worker is not None:
                worker.stop()
            if update_service is not None:
                update_service.stop()
            if noise is not None:
                await noise.stop()"""

content = content.replace(search_str, replace_str)
content = content.replace("lifespan=_create_app_lifespan(cfg, manager),", "lifespan=AppLifespan(cfg, manager),")

with open('server/pivot/api/app.py', 'w') as f:
    f.write(content)
