from rest_framework import status
from rest_framework.exceptions import APIException


class BankingSecurityException(APIException):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "Banking security policy blocked this request."
    default_code = "banking_security"


class MfaNotEnrolledException(BankingSecurityException):
    default_detail = {
        "message": "Multi-factor authentication is not enrolled for this account.",
        "code": "mfa_not_enrolled",
    }
    default_code = "mfa_not_enrolled"


class MfaVerificationNeededException(BankingSecurityException):
    default_detail = {
        "message": "Multi-factor verification is required for this session.",
        "code": "mfa_verification_needed",
    }
    default_code = "mfa_verification_needed"


class MfaRequiredException(BankingSecurityException):
    default_detail = {
        "message": "Stronger authentication is required before linking a bank account.",
        "code": "mfa_required",
    }
    default_code = "mfa_required"


class FinancialConsentRequiredException(BankingSecurityException):
    status_code = status.HTTP_428_PRECONDITION_REQUIRED
    default_detail = {
        "message": "Valid consent for the current privacy policy is required.",
        "code": "financial_consent_required",
    }
    default_code = "financial_consent_required"
