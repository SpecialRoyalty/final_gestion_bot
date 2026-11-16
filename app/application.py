from app.services.context import Context
from app.services.readiness import Readiness
from app.services.monitoring import Monitoring
from app.handlers.screens import Screens
from app.handlers.callbacks import Callbacks
from app.handlers.legacy_payments import LegacyPayments
from app.handlers.router import Router
from app.handlers.payments import PaymentScreens
from app.services.moderation import Moderation
from app.services.scheduler import Scheduler
from app.services.payments import ManualPayments


from app.handlers.welcome import Welcome

class App(Welcome, Readiness, Monitoring, Context, Screens, Callbacks, LegacyPayments, Router,
          Moderation, Scheduler, ManualPayments, PaymentScreens):
    """Composition root: handlers, services and adapters share one application context."""
