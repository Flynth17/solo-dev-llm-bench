"""Shared application state for Solo Dev LLM Bench."""

from src.results import ResultsStore

# Exactly one shared ResultsStore singleton
results_store = ResultsStore()