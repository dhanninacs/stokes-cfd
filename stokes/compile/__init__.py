"""Case compiler: dictionary AST, printer, parser; one writer per dialect (ADR-0008).

Import rule: headless. Pure functions from a model snapshot to a case tree;
never imports stokes.run or any client. Planned: M3.
"""
