# datawiserai

Placeholder package for the official Datawiser Python client library.

This package name has been reserved on PyPI for the upcoming public SDK.  
The real client will provide convenient access to:

- Free float events  
- Free float factors & shares outstanding  
- Reference data (identifiers, listings, metadata)  
- Future Datawiser API endpoints  

## Example (future)

```python
import datawiserai as dw

client = dw.Client(api_key="your_api_key")

events = client.free_float_events("OLP")
print(events)
```

## Status

This package is not yet functional — it only exists to reserve the namespace.  
A full implementation will be released in 2026 alongside the public API.

## Repository

https://github.com/datawiserai/python-client
