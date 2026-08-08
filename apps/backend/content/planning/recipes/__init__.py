"""Planning recipes: how to compose transformations to satisfy an output.

Each recipe builds the step chain for one output type using the generalized
PlanBuilder API (ensure_step / bind_output) and the operation catalog in
``planning.transformations``. Extracted from the planner one at a time.
"""
