from rest_framework.throttling import UserRateThrottle


class BankingSensitiveThrottle(UserRateThrottle):
    scope = "banking_sensitive"


class BankingLinkTokenThrottle(UserRateThrottle):
    scope = "banking_link_token"


