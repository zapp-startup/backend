from rest_framework.throttling import AnonRateThrottle


class WaitlistSignupThrottle(AnonRateThrottle):
    """
    Keep waitlist abuse controls separate from the global anon bucket so
    unrelated public endpoints do not consume this endpoint's rate limit.
    """

    scope = "waitlist_signup"
