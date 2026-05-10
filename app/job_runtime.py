"""Job runtime primitives: claiming, retry, backoff, and worker loop.

The implementation still lives in ``app.jobs`` during the transition, but new
code should import runtime concerns from this module.
"""

from app.jobs import claim_next_job, recover_interrupted_work, run_job, worker_loop  # noqa: F401

