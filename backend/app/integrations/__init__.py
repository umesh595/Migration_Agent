"""Third-party enterprise-system connectors.

Mirrors app/llm/base.py's provider-boundary pattern: one ABC per connector type
in this package, swappable concrete adapters alongside it, callers depend only
on the ABC. See app/integrations/catalog_provider.py for the first one.
"""
