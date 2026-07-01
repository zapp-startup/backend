# Zapp AI Prompt Spec v1

## Goal

Ground AI responses in app-specific user data: budget, goals, categories, subscriptions.

## Input Contract

See: `ai/schemas/parameter_schema_v1.json`

## Output Contract

See: `ai/schemas/response_schema_v1.json`

## System Prompt

See: `ai/prompts/system_prompt_v1.txt`

## Data Mapping (v1)

- profile.monthly_income <- UserRawExplicit.monthly_income
- profile.monthly_fixed_expenses <- UserRawExplicit.monthly_fixed_expenses
- profile.financial_goal <- UserRawExplicit.financial_goal
- profile.risk_tolerance <- UserRawExplicit.risk_tolerance
- profile.budget_style <- UserRawExplicit.budget_style
- spending.category_spend_30d <- aggregate Transactions (last 30 days) by category
- subscriptions <- active/relevant Subscription rows
- purchase_advisor_report <- derived overspending analysis when purchase_advisor_logic is enabled

## Acceptance Criteria

- Prompt response validates against response schema.
- Missing data produces a clarifying question.
- No fabricated numeric claims.
- No secret keys are ever included in model inputs/outputs/logs.
