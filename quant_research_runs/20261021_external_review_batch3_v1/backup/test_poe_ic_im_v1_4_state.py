"""Compatibility entrypoint for the v1.4 persistent-state delivery suite.

The daily runner names this stable test path; the implementation lives in the
state-guards module so the guards remain independently discoverable.
"""

from test_ic_im_v1_4_state_guards import *  # noqa: F401,F403
