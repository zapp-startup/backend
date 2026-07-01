# AI module local testing notes

## Testing chatbot navigation flow locally (PowerShell-friendly)

The purchase-intent chatbot flow returns navigation options (instead of writing directly to DB from chat).

### Start server

```powershell
.\.venv\Scripts\Activate.ps1
python manage.py migrate --settings=config.settings.development
python manage.py runserver --settings=config.settings.development
```

### Create a conversation

```powershell
$headers = @{ "Content-Type" = "application/json"; "X-Dev-User" = "seed_user_1" }
$conversationBody = @{ context_type = "budgeting"; title = "PowerShell nav test" } | ConvertTo-Json
$conversation = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/ai/conversations/" -Headers $headers -Body $conversationBody
$conversation.conversation_id
```

### Send a purchase-style message

```powershell
$messageBody = @{ content = "I bought coffee at Starbucks for $6.50 today" } | ConvertTo-Json
$response = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/ai/conversations/$($conversation.conversation_id)/messages/" -Headers $headers -Body $messageBody
$response.assistant_message.metadata_json | ConvertTo-Json -Depth 8
```

Expected metadata includes:
- `intent_detection.intent = "record_transaction"`
- `action = "navigate_to_data_entry"`
- `action_status = "routing_options"`
- `created_transaction_id = null`
- `quick_actions` with routes like `/transactions/new`, `/subscriptions`, `/subscriptions/new`, `/valuations/new`

## Rate limiting

Django REST Framework throttling is enabled by default for the backend. You can tune the limits with these optional environment variables:

- `API_THROTTLE_ANON_RATE` (default: `30/minute`)
- `API_THROTTLE_USER_RATE` (default: `120/minute`)
- `API_THROTTLE_AI_RATE` (default: `20/minute`)

The AI conversation endpoints use the stricter `API_THROTTLE_AI_RATE` scope, while the rest of the API continues to use the general anonymous/authenticated API limits.
