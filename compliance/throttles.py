from rest_framework.throttling import UserRateThrottle


class ComplianceConsentThrottle(UserRateThrottle):
    scope = "compliance_consent"
