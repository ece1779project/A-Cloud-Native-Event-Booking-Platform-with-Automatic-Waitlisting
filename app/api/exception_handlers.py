"""Maps ``app.core.errors`` exceptions, validation errors and unexpected
failures to the error envelope and HTTP status.

Expected business rejections are not logged as server errors.

Design: TECHNICAL_DESIGN.md Sections 8.1 and 8.5.
"""
