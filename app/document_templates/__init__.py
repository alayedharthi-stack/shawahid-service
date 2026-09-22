"""Opt-in document templates. No routes, delivery, database or evidence writes."""

from .renderer import render_document, export_pdf, validate_document

__all__ = ["render_document", "export_pdf", "validate_document"]
