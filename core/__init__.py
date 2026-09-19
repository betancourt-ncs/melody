"""Core layer: checks, the model they produce, and the pipeline that runs them.

Plain Python. Knows nothing about how melody is invoked. Both adapters -- cli.py
and mcp_server.py -- call into this package, and it imports neither of them.
"""
