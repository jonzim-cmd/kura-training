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
from . import custom_projection  # noqa: F401
from . import user_profile  # noqa: F401
from . import router  # noqa: F401
