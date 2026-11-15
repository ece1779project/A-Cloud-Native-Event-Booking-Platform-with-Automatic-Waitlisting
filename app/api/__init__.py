"""HTTP transport layer.

Route handlers validate transport data, call one service, and publish the
committed result. They hold no business rule, open no transaction and take
no lock.

Design: TECHNICAL_DESIGN.md Sections 5 and 8.
"""
