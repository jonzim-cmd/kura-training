# Import all handlers so they register themselves.
# ORDER MATTERS: user_profile must be imported AFTER other dimension handlers.
# The router dispatches handlers in registration order, so user_profile runs last
# and can read projections written by earlier handlers within the same transaction.
from . import exercise_progression  # noqa: F401
from . import training_timeline  # noqa: F401
from . import body_composition  # noqa: F401
from . import recovery  # noqa: F401
from . import nutrition  # noqa: F401
from . import training_plan  # noqa: F401
from . import semantic_memory  # noqa: F401
from . import strength_inference  # noqa: F401
from . import readiness_inference  # noqa: F401
from . import causal_inference  # noqa: F401
from . import session_feedback  # noqa: F401
from . import inference_nightly  # noqa: F401
from . import external_import  # noqa: F401
from . import custom_projection  # noqa: F401
from . import quality_health  # noqa: F401
from . import consistency_inbox  # noqa: F401
from . import open_observations  # noqa: F401
from . import account_lifecycle  # noqa: F401
from . import log_retention  # noqa: F401
from . import user_profile  # noqa: F401
from . import router  # noqa: F401
