Client request example:
```json
{
  // Unique identifier for this request (used to track response)
  "id": "req-20250616-001",           
  // Main prompt or query
  "query": "What is the capital of France?",  
  // (Optional) Client operating system info
  "os": "windows",                    
  // (Optional) OS version or application version
  "version": "11",                    
  // (Optional) Any additional metadata
  "meta": {                           
    "user_id": "user-1234",
    "timestamp": "2025-06-16T13:45:00Z",
    "source": "web-app"
  }
}
```